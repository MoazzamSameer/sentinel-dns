# DoH upstream

Until this task, the forwarder shipped queries upstream over plain UDP/53. The ISP can see every domain you query that way — even if you trust the upstream resolver (Cloudflare, Quad9), the wire is plaintext to anyone in between. The architecture committed to DoH (DNS-over-HTTPS) as the default upstream transport for v0.1; this is the wiring.

## Behavior

```
$ sentinel-dns --upstream-doh-url https://cloudflare-dns.com/dns-query
... listening on 127.0.0.1:5354, upstream=DoH https://cloudflare-dns.com/dns-query, ...
```

When `upstream_doh_url` is set (CLI flag or TOML key), every forwarded query goes out as an HTTPS POST. The ISP sees you talking to Cloudflare's edge over TLS but **not which domains you query**. When the field is unset, the forwarder uses the existing UDP path — backwards compatible.

`upstream_host` and `upstream_port` are still honored when DoH is off. They're not used when DoH is on; the URL carries everything.

## Implementation

In [`sentinel_dns/forwarder.py`](../sentinel_dns/forwarder.py):

1. **Dispatch in `_forward`.** If `config.upstream_doh_url` is set, call `dns.asyncquery.https()`; otherwise the existing `dns.asyncquery.udp()`.
2. **Shared httpx client** (this turned out to be load-bearing — see *Performance*). Created in `serve()` with `http2=True`, lives for the forwarder's lifetime, passed to every `https()` call. Closes cleanly on shutdown.
3. **HTTP/2 explicitly pinned.** dnspython's default for `https()` tries HTTP/3 first, which needs `aioquic`. Pinning to `HTTPVersion.HTTP_2` avoids the failed-probe latency on every startup.
4. **Broader `_forward` exception catch.** Network failures from httpx (`ConnectError`, `HTTPError`, etc.) don't subclass any of the dns exceptions we previously caught. Left uncaught they crashed the per-query task silently. Now caught; SERVFAIL returned to client; structured warning logged.

## Performance

Three-way bench: direct UDP to 1.1.1.1, forwarder over UDP, forwarder over DoH. 240 samples per path, interleaved.

```
direct (UDP to 1.1.1.1):     p50= 37.58ms
forwarder UDP upstream:      p50= 39.22ms  (+1.64ms vs direct — matches prior measurements)
forwarder DoH upstream:      p50= 75.83ms  (+38.24ms vs direct, +36.61ms vs UDP forwarder)
```

**~36ms p50 overhead vs UDP** in this single-machine measurement. Higher than the ~5–20ms I initially expected. The breakdown:
- ~10–15ms network RTT to Cloudflare's edge (unavoidable)
- HTTP/2 stream setup + framing
- TLS encrypt/decrypt
- Python httpx + h2 overhead

### The shared-client fix

The first version of this PR didn't pass a `client=` argument to `dns.asyncquery.https()`. dnspython then created a fresh `httpx.AsyncClient` per query — meaning a fresh TLS handshake per query. Latency was **+118ms p50**. Adding the shared client dropped it to +36ms p50, a 3× improvement. Worth keeping in the writeup because the fix isn't documented in dnspython's main examples.

### Tail latency

```
direct p99: 181.82ms        (UDP, network jitter)
UDP fwd p99: 222.18ms
DoH fwd p99: 126.71ms       (lower than direct in this run)
```

The DoH forwarder beating direct UDP at p99 is network jitter — UDP got a worse few queries during its window. Don't over-read it. The microbench-level point is: **DoH p99 is plausibly within the same envelope as UDP p99 once the connection is warm.**

## Verification

End-to-end with `dig`:

```
$ sentinel-dns --upstream-doh-url https://cloudflare-dns.com/dns-query
$ dig @127.0.0.1 -p 5354 google.com +short
142.251.223.142
$ dig @127.0.0.1 -p 5354 github.com +short
20.207.73.82
```

Bad URL returns SERVFAIL cleanly:

```
$ sentinel-dns --upstream-doh-url https://nonexistent.invalid/dns-query --upstream-timeout 2
$ dig @127.0.0.1 -p 5354 example.com
;; status: SERVFAIL

(forwarder log)
WARNING upstream error (ConnectError): [Errno 8] nodename nor servname provided, or not known
```

## Caveats

1. **~36ms p50 overhead is from a single dev machine.** ISP, network conditions, and DoH endpoint location all change this materially. Cloudflare's edge is closer to me than e.g. Quad9 globally. Re-bench on the actual deployment hardware before tuning thresholds.
2. **HTTP/2 pinned, no HTTP/3.** dnspython supports HTTP/3 via `aioquic` but it adds a heavyweight dependency and the latency benefit over HTTP/2 in this single-stream use case is marginal. Revisit if there's user demand.
3. **Single shared client.** No per-endpoint client pool yet — if you ever wanted to split traffic across multiple DoH endpoints, you'd want multiple clients. Out of v0.1 scope.
4. **No DoT (DNS-over-TLS) support.** TCP/853 is a different transport from HTTPS/443; we picked DoH because the architecture's research wedge is "explanations + lexical detection", not "broadest transport coverage." DoT could land later if asked.
5. **No fallback from DoH to UDP on failure.** If DoH is unreachable, every query SERVFAILs until DoH recovers. A "DoH first, UDP fallback" mode is nice-to-have but introduces fingerprinting risk (the upstream resolver can tell when DoH fails based on which protocol you arrive on). Defer.
6. **httpx is now a base dependency.** Plus `httpx[http2]` extra (pulls in `h2`). ~3 MB additional dep weight. Reasonable cost for the privacy upgrade.
7. **Connection pool stays open** — even when no queries are flowing, the TLS session is held. Cloudflare may close it on their side after a few minutes of idleness; httpx handles re-establishment transparently. Worst case: one slow query after a long idle period.

## What this unblocks

- The privacy claim from RESEARCH.md is now real on the wire, not just on the box. ISP can no longer see the user's domain queries when DoH is on.
- The README quickstart can recommend a privacy-respecting default config.
- DoH is the default for the upcoming Docker image — the example config now suggests uncommenting the DoH line.
