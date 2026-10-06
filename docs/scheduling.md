# Scheduling

GitHub's built-in schedule is best effort. In practice Peg's evening jobs ran
2–6 hours late, and the evening question once ran after midnight and asked
about the wrong day. So the two time-sensitive jobs are started by an outside
scheduler, cron-job.org, which calls GitHub's "run workflow" API on time.

| Job | Workflow file | Started by | UK time |
|---|---|---|---|
| Forecast for tomorrow | `peg.yml` | cron-job.org (+ GitHub schedule as backup) | 17:00 |
| "Did it dry?" question | `peg-evening.yml` | cron-job.org only | 19:30 |
| Catch button answers | `peg-outcome.yml` | GitHub schedule | evenings and mornings |
| Weekly report | `peg-summary.yml` | GitHub schedule | Monday morning |

The forecast cannot be sent twice: if tomorrow is already in the Prediction
Log, the second run stops. The evening question has no backup, because it has
no such check.

## The call cron-job.org makes

```
POST https://api.github.com/repos/jimwarrilow-max/Peg/actions/workflows/<file>/dispatches
Authorization: Bearer <token>
Accept: application/vnd.github+json
X-GitHub-Api-Version: 2022-11-28

{"ref":"main"}
```

GitHub answers `204 No Content` when it worked.

`<token>` is a fine-grained personal access token with access to this
repository only and one permission: **Actions: Read and write**. It is stored
only in cron-job.org. When it expires, the calls fail and cron-job.org sends
an email; make a new token and paste it into both cron-job.org jobs.
