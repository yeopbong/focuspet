# Data dictionary

The SQLite schema version is tracked by `PRAGMA user_version`. The four principal tables are `settings`, `events`, `features`, and `state_feedback`; `policy_state` provides policy persistence. Event envelopes have `id`, `kind`, UTC `start/end/created_at`, `session_id`, optional `episode_id`, `mode`, `schema` and a JSON payload. Modes are `real`, `synthetic-demo` and `test`. Native buckets additionally preserve a display timezone; raw activity never contains bundle IDs or window titles.

| Entity | Key fields and meaning |
|---|---|
| UserSettings / Profile | Consent, chosen character, changeable cold-start profile, display position/scale, category overrides, quiet controls, retention, explicit startup opt-in. Profile changes produce no labels. |
| ActivityBucket | Disjoint fresh `duration_s`; nullable keyboard/click/scroll/pointer counts, `idle_s`, category dwell seconds, application switch count, per-signal `coverage`, observation, `interaction_s`, missing reason. |
| FeatureWindow | Rolling causal 60-second values and 300-second context, schema/order, source bucket references, valid duration, coverage, session and mode. The window overlaps previous windows and is never summed for total time. |
| PredictionEvent | Normalized Focused/Normal/Distracted components, accepted or Unknown state, source, model version, normalized entropy, coverage, actual signal-based reasons, feature reference. Components are uncalibrated. |
| StateSnapshot | A disjoint display interval, observed state, inferred work state, smoothed Focus or null, continuous load, stale flag, parameter version, prediction and feature references. |
| WorkloadStep | Frozen already-estimated components, fresh effective duration, valid/rest flags, actual value before/after, model-context interval, parameter version and explicit resets. This is the source for BBO history replay. |
| WorkloadEvent | Display-level load value, stale flag, rest flag and prediction/parameter references; not counted again in BBO or total duration. |
| StateFeedback | Target interval, label, label time, user/audit/active-query source, independent episode, revision relationship, withdrawal and trainability flags, original prediction references and model versions. |
| LoadFeedback | Explicit Yes/No/Not sure to wanting rest; UTC time, independent session, optional rest reference. Unanswered/skipped does not become No. |
| RestSession | User declaration with start, bounded end and minutes, or explicit retrospective confirmation. End event can shorten a declaration. |
| NotificationEvent | Idempotent ID, A2/A3 action, reason text, timestamp, mechanism, policy version. A0 is no event; A1 is local companion animation. |
| Query / QueryResponse | Completed target interval, selection mechanism/version/reason, display time, answer or missing outcome. Random audits hide the prediction before answering. |
| ModelVersion | Numeric artifact and registry with training schema/order/scaler/classes, source episodes, comparisons, shadow/active/rejected states and timestamps. |
| ParameterVersion | Fixed two-parameter domain model, searches/trial budgets/seeds/losses, source reports, candidate/active state and effective time. |

Observed states are Active, No-input, Locked, Paused, Missing. User work states are Focused, Normal, Distracted, Rest, Unknown. Low input alone is not Rest or Distracted. Lock and missing intervals are not inferred recovery. Focus is a 0–100 engagement estimate, entropy is uncertainty and coverage is availability; these fields are separate.

Within an overlapping feedback episode, only its latest effective label is used. Subwindows carry equal total episode weight. Original predictions remain immutable. Expiring source features marks feedback untrainable; deleting dependent data invalidates affected application-owned artifacts. The default export omits settings and retains opaque relationship identifiers while preserving useful timing and numeric aggregate history.
