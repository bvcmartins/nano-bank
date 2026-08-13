//! Carrying a caller-supplied decision instant from the HTTP request down to
//! the fraud gate.
//!
//! **Why a task-local and not a parameter.** `screen()` is called from eight
//! sites across six handlers, none of which take a `HeaderMap` today. Threading
//! an `Option<DateTime<Utc>>` through all of them — and through `ScreenInput`,
//! and through `execute_transfer` — would touch every rail to deliver a value
//! that only a simulation deployment ever sets. The middleware scopes it for
//! the request's task instead, and the gate reads it in the one place
//! `FraudRequest` is built.
//!
//! **This module transports; it does not decide.** Parsing here is
//! unconditional and deliberately harmless: the value is inert until
//! `fraud::gate::screen()` consults `accept_simulated_time`. Keeping the
//! security check next to the `FraudRequest` construction means a reviewer
//! looking for the boundary finds it where the request is assembled, rather
//! than in a middleware they would have to go looking for.

use axum::{extract::Request, middleware::Next, response::Response};
use chrono::{DateTime, Utc};

tokio::task_local! {
    static SIMULATED_TIME: Option<DateTime<Utc>>;
}

/// RFC 3339, e.g. `2024-04-01T09:15:00Z`.
pub const HEADER: &str = "x-simulated-time";

/// Parse the header once per request and scope it to the handler's task.
///
/// A malformed value becomes `None` rather than a 400. This header is a
/// screening affordance for replay; letting a bad one refuse the movement would
/// turn a simulation convenience into a way to break payments.
pub async fn capture(request: Request, next: Next) -> Response {
    let raw = request
        .headers()
        .get(HEADER)
        .and_then(|value| value.to_str().ok())
        .map(str::to_string);

    SIMULATED_TIME
        .scope(parse(raw.as_deref()), next.run(request))
        .await
}

/// RFC 3339 or nothing. Split out from the middleware so the parsing has tests
/// that do not need a router.
pub fn parse(raw: Option<&str>) -> Option<DateTime<Utc>> {
    raw.and_then(|value| DateTime::parse_from_rfc3339(value).ok())
        .map(|parsed| parsed.with_timezone(&Utc))
}

/// The instant to measure a decision at: the caller's, or the engine's own.
///
/// This one function is the entire security argument, so it is separate and
/// tested rather than inline at the call site. When the deployment has not
/// opted in, whatever the caller sent is discarded unread — velocity windows
/// are the engine's primary lever, and a caller that can choose its own window
/// can make any of them abstain.
pub fn decision_instant(
    accept_simulated_time: bool,
    supplied: Option<DateTime<Utc>>,
) -> Option<DateTime<Utc>> {
    if accept_simulated_time {
        supplied
    } else {
        None
    }
}

/// The instant this request supplied, if any.
///
/// Returns `None` outside the middleware's scope — background tasks, the outbox
/// drain, unit tests — rather than panicking, because a caller-supplied clock
/// is optional everywhere by construction.
pub fn supplied() -> Option<DateTime<Utc>> {
    SIMULATED_TIME.try_with(|value| *value).unwrap_or(None)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn outside_the_middleware_there_is_no_supplied_time() {
        // The outbox drain and the rescore path call screen()-adjacent code
        // from tasks the middleware never wrapped. try_with must degrade, not
        // panic: a panic here would take down a background worker.
        assert!(supplied().is_none());
    }

    #[tokio::test]
    async fn the_scoped_value_is_visible_to_the_task() {
        let instant = at("2024-04-01T09:15:00Z");
        SIMULATED_TIME
            .scope(Some(instant), async {
                assert_eq!(supplied(), Some(instant));
            })
            .await;
    }

    fn at(raw: &str) -> DateTime<Utc> {
        DateTime::parse_from_rfc3339(raw)
            .unwrap()
            .with_timezone(&Utc)
    }

    #[test]
    fn off_is_the_default_and_it_discards_whatever_the_caller_sent() {
        // The test that matters. Everything else here pins a branch; this pins
        // which branch an un-opted-in deployment takes. A caller parking every
        // request in a quiet window would make velocity rules abstain — and
        // velocity is the engine's primary lever, so that is the whole reason
        // this is off unless someone turns it on.
        assert_eq!(
            decision_instant(false, Some(at("2020-01-01T00:00:00Z"))),
            None
        );
        assert!(!crate::config::FraudSettings::default().accept_simulated_time);
    }

    #[test]
    fn on_the_supplied_instant_is_used() {
        let instant = at("2024-04-01T09:15:00Z");
        assert_eq!(decision_instant(true, Some(instant)), Some(instant));
        // Opted in but nothing sent is still the engine's own clock.
        assert_eq!(decision_instant(true, None), None);
    }

    #[test]
    fn a_malformed_header_falls_back_rather_than_refusing_the_movement() {
        // Deliberately not an error. This header is a replay affordance; making
        // a bad one refuse the payment would turn a simulation convenience into
        // a way to break money movement.
        assert_eq!(parse(Some("yesterday")), None);
        assert_eq!(parse(Some("")), None);
        assert_eq!(parse(None), None);
    }

    #[test]
    fn an_offset_timestamp_is_normalised_to_utc() {
        // The world model emits local wall-clock times with an offset; if these
        // were read as UTC every simulated day would land in the wrong window.
        assert_eq!(
            parse(Some("2024-04-01T05:15:00-04:00")),
            Some(at("2024-04-01T09:15:00Z"))
        );
    }
}
