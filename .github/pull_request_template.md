## Summary

Describe the change and why it is needed.

## Security impact

Check every item that applies:

- [ ] This change does not broaden filesystem access.
- [ ] This change does not broaden executable/command access.
- [ ] This change does not expose environment variables, credentials, tokens, or local configuration.
- [ ] All new paths are canonicalized and constrained to an allowed project root.
- [ ] New process execution uses an argument vector with shell interpretation disabled.
- [ ] Timeouts, output limits, and concurrency limits remain enforced.
- [ ] Destructive Git/filesystem operations are not introduced, or are explicitly documented and tested.
- [ ] Negative/escape-path tests were added for any broadened capability.
- [ ] No real `.env`, machine paths, credentials, logs, runtime state, or private artifacts are included in the diff.

## Testing

Describe tests run and relevant results.

## Threat-model notes

If this changes the trust boundary or adds a new capability, explain the new abuse cases and mitigations.
