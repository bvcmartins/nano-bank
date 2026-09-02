"use server";

import { cookies } from "next/headers";
import { revalidatePath } from "next/cache";
import { API_BASE_URL, AGENT_API_URL, AGENT_SERVICE_TOKEN } from "@/lib/config";

export interface PendingAction {
  id: string;
  kind: string;
  amount: string;
  from: string;
  to: string;
  summary: string;
}

export interface AgentChatResult {
  success: boolean;
  reply: string;
  threadId?: string;
  pendingAction?: PendingAction;
}

interface AgentMessageBody {
  answer: string;
  thread_id: string;
  pending_action?: {
    id: string;
    kind: string;
    amount: string;
    from: string;
    to: string;
    summary: string;
  };
}

/** The MCP gateway resolves the customer from the cid in the URL and answers
 * with the service token alone — it never re-checks that cid belongs to the
 * caller. So cid always comes from OUR verified access-token cookie via the
 * bank API, never from client input, or any signed-in customer could read or
 * message on behalf of any other customer_id. */
async function resolveCustomerId(accessToken: string): Promise<string | null> {
  try {
    const response = await fetch(`${API_BASE_URL}/api/v1/customers/profile`, {
      headers: { Authorization: `Bearer ${accessToken}` },
      cache: "no-store",
    });
    if (!response.ok) return null;
    const body = await response.json();
    return typeof body.customer_id === "string" ? body.customer_id : null;
  } catch (error) {
    console.error("Failed to resolve customer profile for agent chat:", error);
    return null;
  }
}

/** Over HTTP the MCP tool results agent/api.py's confirm/cancel routes return
 * come back as content blocks ([{"type":"text","text":"<json>"}]) rather than
 * plain JSON — unwrap defensively so the UI never shows raw blocks. */
function unwrapMcp(result: unknown): Record<string, unknown> | unknown {
  if (Array.isArray(result) && result.length > 0) {
    const block = result[0] as { text?: unknown };
    if (block && typeof block === "object" && typeof block.text === "string") {
      try {
        return JSON.parse(block.text);
      } catch {
        return block.text;
      }
    }
  }
  return result;
}

async function requireCustomerId(): Promise<
  { customerId: string; accessToken: string } | { error: AgentChatResult }
> {
  if (!AGENT_SERVICE_TOKEN) {
    return { error: { success: false, reply: "The assistant isn't connected yet — missing AGENT_SERVICE_TOKEN." } };
  }

  const cookieStore = await cookies();
  const accessToken = cookieStore.get("access_token")?.value;
  if (!accessToken) {
    return { error: { success: false, reply: "Your session has expired. Please sign in again." } };
  }

  const customerId = await resolveCustomerId(accessToken);
  if (!customerId) {
    return { error: { success: false, reply: "Unable to verify your session. Please sign in again." } };
  }

  return { customerId, accessToken };
}

export async function sendAgentMessageAction(message: string, threadId?: string): Promise<AgentChatResult> {
  if (!message.trim()) {
    return { success: false, reply: "Please enter a message." };
  }

  const resolved = await requireCustomerId();
  if ("error" in resolved) return resolved.error;

  let response: Response;
  try {
    response = await fetch(`${AGENT_API_URL}/branch/clients/${resolved.customerId}/message`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        Authorization: `Bearer ${AGENT_SERVICE_TOKEN}`,
        "X-Nano-Customer-Token": resolved.accessToken,
      },
      body: JSON.stringify({ message, thread_id: threadId }),
      cache: "no-store",
      signal: AbortSignal.timeout(60_000),
    });
  } catch (error) {
    console.error("Agent chat request failed:", error);
    return { success: false, reply: "Unable to reach the assistant. Please try again." };
  }

  if (!response.ok) {
    console.error(`Agent chat request failed with status ${response.status}`);
    return { success: false, reply: "The assistant had trouble with that. Please try again." };
  }

  const body: AgentMessageBody = await response.json();
  return {
    success: true,
    reply: body.answer,
    threadId: body.thread_id,
    pendingAction: body.pending_action
      ? {
          id: body.pending_action.id,
          kind: body.pending_action.kind,
          amount: body.pending_action.amount,
          from: body.pending_action.from,
          to: body.pending_action.to,
          summary: body.pending_action.summary,
        }
      : undefined,
  };
}

async function respondToAction(actionId: string, verb: "confirm" | "cancel"): Promise<AgentChatResult> {
  const resolved = await requireCustomerId();
  if ("error" in resolved) return resolved.error;

  let response: Response;
  try {
    response = await fetch(
      `${AGENT_API_URL}/branch/clients/${resolved.customerId}/actions/${actionId}/${verb}`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${AGENT_SERVICE_TOKEN}`,
          "X-Nano-Customer-Token": resolved.accessToken,
        },
        cache: "no-store",
        signal: AbortSignal.timeout(30_000),
      }
    );
  } catch (error) {
    console.error(`Agent action ${verb} failed:`, error);
    return { success: false, reply: `Unable to ${verb} that action. Please try again.` };
  }

  if (!response.ok) {
    console.error(`Agent action ${verb} failed with status ${response.status}`);
    return { success: false, reply: `Unable to ${verb} that action. Please try again.` };
  }

  const result = unwrapMcp(await response.json().catch(() => ({})));
  if (result && typeof result === "object" && "error" in result) {
    return { success: false, reply: `Couldn't ${verb} that: ${(result as { error: string }).error}` };
  }

  // A confirmed action may have moved money (transfer, e-transfer send) —
  // revalidate so the account summary/list pick up the new balances next
  // time they're rendered.
  if (verb === "confirm") {
    revalidatePath("/dashboard");
    revalidatePath("/dashboard/accounts");
  }

  return {
    success: true,
    reply: verb === "confirm" ? "Done — that action has been executed." : "That action has been cancelled.",
  };
}

export async function confirmAgentActionAction(actionId: string): Promise<AgentChatResult> {
  return respondToAction(actionId, "confirm");
}

export async function cancelAgentActionAction(actionId: string): Promise<AgentChatResult> {
  return respondToAction(actionId, "cancel");
}
