"""Red-team concurrency stress test: same session_id, different chat_id.

Verifies the session lock is keyed on the (session_id, chat_id) PAIR — only the
same pair is mutually exclusive. Same session_id with different chat_id must NOT
block each other or leak session data.

Run:
    .\\.venv\\Scripts\\python.exe redteam_pair_concurrency.py
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:18765"
TOKEN = "dev-token-0001"
SHARED_SID = "s-concurrent-01"
CONCURRENCY = 8        # threads; raise to 16/32 for heavier load
ROUNDS_PER_WORKER = 5  # rounds per worker

FAILURES: list[str] = []
LOCK = threading.Lock()


def req(method: str, path: str, body: dict | None = None):
    r = urllib.request.Request(BASE + path, method=method)
    r.add_header("Authorization", "Bearer " + TOKEN)
    if body is not None:
        r.add_header("Content-Type", "application/json")
        r.data = json.dumps(body).encode("utf-8")
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {"error": str(e)}
    except Exception as e:
        return None, {"error": str(e)}


def worker(worker_idx: int) -> None:
    cid = hashlib.sha256(f"pairtest_w{worker_idx}".encode()).hexdigest()
    wid_tag = f"w{worker_idx}"
    print(f"[{wid_tag}] start, chat_id={cid[:16]}...")

    for r in range(ROUNDS_PER_WORKER):
        try:
            # ASK
            st, out = req("POST", "/api/v1/plan/session/ask", {
                "session_id": SHARED_SID,
                "chat_id": cid,
                "root_seq": "10",
                "requirement": f"worker {wid_tag} round {r}",
            })
            if st != 200:
                raise AssertionError(f"ASK fail w{worker_idx} r{r}: {out}")
            retrieved_cid = out.get("chat_id")
            if retrieved_cid != cid:
                raise AssertionError(
                    f"DATA LEAK! w{worker_idx} got chat_id={retrieved_cid}, expect {cid}"
                )

            # CONFIRM
            st, out = req("POST", "/api/v1/plan/session/confirm", {
                "session_id": SHARED_SID, "chat_id": cid,
            })
            if st != 200:
                raise AssertionError(f"CONFIRM fail w{worker_idx} r{r}: {out}")

            # GENERATE
            st, out = req("POST", "/api/v1/plan/session/generate", {
                "session_id": SHARED_SID, "chat_id": cid,
            })
            if st != 200:
                raise AssertionError(f"GENERATE fail w{worker_idx} r{r}: {out}")
            gen_cid = out.get("chat_id")
            if gen_cid != cid:
                raise AssertionError(
                    f"GENERATE DATA LEAK w{worker_idx}: got {gen_cid}"
                )

            print(f"[{wid_tag}] round{r} OK")
            time.sleep(0.1)
        except Exception as e:
            with LOCK:
                FAILURES.append(f"w{worker_idx} r{r}: {repr(e)}")
            print(f"[{wid_tag}] round{r} FAILED: {repr(e)}")
            return
    print(f"[{wid_tag}] ALL ROUNDS COMPLETE")


def main() -> int:
    threads = []
    start = time.time()
    for i in range(CONCURRENCY):
        t = threading.Thread(target=worker, args=(i,))
        threads.append(t)
        t.start()
    for t in threads:
        t.join()
    elapsed = time.time() - start
    print(f"\nTest done. Total time: {elapsed:.2f}s")
    if FAILURES:
        print(f"FAILURES ({len(FAILURES)}):")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("PASS: no DATA LEAK, no timeouts, no version conflicts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())