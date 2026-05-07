# Enforcement mode

Until this task, the inline classifier was measurement-only — it logged `would_block=True` for malicious domains but still forwarded the query upstream and returned the real answer. Useful for measurement and cache warmup, but it's not yet a security tool.

This adds the `--enforce` flag. When set, queries the classifier flags as malicious get **NXDOMAIN** instead of being forwarded.

## Behavior

```
$ sentinel-dns --model-path models/classifier_v0.joblib --enforce
... listening on 127.0.0.1:5354, ... enforce=on

$ dig @127.0.0.1 -p 5354 google.com +short
142.250.182.46

$ dig @127.0.0.1 -p 5354 1ce6-route.fixionmunici9al.lat +short
;; status: NXDOMAIN
```

The block decision uses the same threshold (`--block-threshold`, default 0.836) as measurement mode — the 0.1% FPR operating point from [Spike B](spike-b-results.md). At that threshold, the classifier was 95% precise on held-out URLhaus.

## Log lines distinguish blocks from allows

```
INFO ... score qname=google.com ml=0.0274 heur=0.000 would_block=False cache=miss inline_us=2161.7
INFO ... BLOCK qname=1ce6-route.fixionmunici9al.lat ml=0.9980 heur=0.300 would_block=True cache=miss inline_us=699.7
INFO ... BLOCK qname=1ce6-route.fixionmunici9al.lat ml=0.9980 heur=0.300 would_block=True cache=hit
```

Three things to notice:

1. The prefix changes from `score` to `BLOCK` only when `--enforce` is on AND `would_block` is true. In measurement mode (`--enforce` off), all entries say `score` regardless of `would_block`. This makes it easy to grep for actual blocks vs scoring telemetry.
2. Cache hits short-circuit the classifier — the second query for the same domain shows `cache=hit` with no `inline_us` field. The cache works the same whether we're enforcing or not.
3. The `would_block=True` field is preserved on `BLOCK` lines — redundant with the prefix, but it lines up the field set across all log entries which makes downstream parsing easier.

## NXDOMAIN, not REFUSED or sinkhole

Three reasonable responses for "I don't want this domain to resolve":

| Response | What it tells the client |
|---|---|
| **NXDOMAIN** *(picked)* | "This name doesn't exist" — terminal, clients stop trying immediately |
| REFUSED (rcode 5) | "I refuse to answer" — clients may retry against another resolver |
| Sinkhole IP (e.g. 0.0.0.0) | "Here's an answer that won't connect anywhere" — works for HTTP, breaks oddly for HTTPS / cert-pinned |

Pi-hole defaults to sinkhole, Quad9 returns NXDOMAIN. We follow Quad9 — NXDOMAIN is what most security-oriented resolvers do, plays well with DoT/DoH downstream clients, and avoids the sinkhole-IP traps. The choice is hardcoded for v0.1; a `--block-mode` flag is post-v0.1.

## Safety

- `--enforce` requires `--model-path` — argparse errors out otherwise. Nothing to enforce against without a classifier.
- Off by default. The natural deployment flow is: run in measurement mode for a day or two, look at what *would* be blocked, then flip `--enforce` once you trust the classifier's behavior on your actual traffic.
- Block threshold is configurable. The default (0.836, ≈0.1% FPR on the spike B test set) is conservative — tighten or loosen per deployment.

## Caveats

1. **No allow-list yet.** If the classifier ever flags a benign domain you care about (the spike showed `malwarebytes.com` getting elevated scores due to the substring "malware"), there's no override mechanism in v0.1. Adding allowlist is a follow-up task.
2. **No "why was this blocked" explanation surfaced to users.** The structured log shows `ml=` and `heur=` numeric scores, but there's no plain-English reason — that's the next task on PROJECT.md.
3. **No metrics or alerting.** Block counts go to log lines only. A counter / Prometheus endpoint is post-v0.1.
4. **No bypass for emergencies.** Once enforce is on, a misclassified domain stays blocked until the operator restarts the forwarder (clearing cache) or extends the configuration (allowlist). That's fine for v0.1 but worth knowing.
5. **Not yet hooked to the static blocklist.** The classifier is the only blocker. The static-blocklist-from-URLhaus task adds a second source of block decisions ahead of the classifier.

## Verification

The smoke test against the live forwarder produced the expected output:

```
benign:    google.com → NOERROR (real IP)
benign:    github.com → NOERROR (real IP)
malicious: 1ce6-route.fixionmunici9al.lat → NXDOMAIN
malicious: 5cri-logic.xamir3on.lat → NXDOMAIN
```

Both URLhaus domains had `ml=0.998+`, well above the 0.836 threshold. Both benign domains had `ml<0.05`, well below.

Cache short-circuiting was also verified: the second query for the same malicious domain hit the cache and produced an instant NXDOMAIN without re-running the classifier.

## What this unblocks

The forwarder is now a real security tool — point a device at it, turn on `--enforce`, and it'll actually block malware domains. The remaining v0.1 work makes the experience usable (explanations, query log, CLI tools, distribution) but the security function exists from this PR onward.
