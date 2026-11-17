"""The one output shape every check uses: time, check name, PASS / FAIL / SKIP, then a reason or name=value pairs."""
import time

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"


def emit(group, results, results_log="", quiet=False):
    """Print (and append to results_log, if set) one line per (name, status, detail). Returns the exit code:
    1 when any result is FAIL."""
    stamp = time.strftime("%F %T")
    lines = [(status, f"{stamp} {group}.{name} {status} {detail}") for name, status, detail in results]
    if results_log:
        with open(results_log, "a") as fh:
            fh.writelines(line + "\n" for _, line in lines)
    for status, line in lines:
        if status == FAIL or not quiet:
            print(line)
    return 1 if any(status == FAIL for status, _ in lines) else 0
