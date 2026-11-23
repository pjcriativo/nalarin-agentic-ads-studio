"""Entry point for the SEPARATE-PROCESS worker tests: ``python -m tests.unit.worker_launcher [--once]``.

It is the real ``app.worker`` CLI, with one difference: the AI Gateway factory is replaced by a scripted gateway read from
the environment, so no provider is ever contacted. Test-only: nothing in ``app`` can load this module.

Environment:
  WORKER_SCRIPT  JSON list replayed in order, shared by every task of this process. Items: "plan" | "product" | "copy" |
                 "report" | {"sleep": seconds, "then": <item>} | {"error": "<ai error kind>"}.
  ECHO_DELAY     without a script: answer every ``agent.<key>`` call with that agent's valid output after this many seconds.
  CALLS_FILE     every model call writes ``<org>|<operation>`` to its own ``<CALLS_FILE>.<ns>-<n>-<pid>`` file (one file per
                 call: concurrent appends from several processes are not atomic on every OS). Read with ``calls()`` in the tests.
"""
import itertools
import json
import os
import sys
import time

from app.agents import executor
from app.worker.worker import cli
from tests.unit.agents_support import (COPY, OUTPUT_FOR, PLAN, PRODUCT, REPORT, Delay, ScriptedGateway, ai_error, say_json)

_COUNTER = itertools.count()
_NAMED = {"plan": PLAN, "product": PRODUCT, "copy": COPY, "report": REPORT}


def _item(spec):
    if isinstance(spec, dict) and "sleep" in spec:
        return Delay(float(spec["sleep"]), _item(spec["then"]))
    if isinstance(spec, dict) and "error" in spec:
        return ai_error(spec["error"])
    return say_json(_NAMED[spec])


class RecordingGateway(ScriptedGateway):
    def __init__(self, org_id, echo_delay, *script):
        super().__init__(*script)
        self.org_id, self.echo_delay = org_id, echo_delay

    async def chat(self, operation, request):
        path = os.environ.get("CALLS_FILE")
        if path:
            with open(f"{path}.{time.time_ns()}-{next(_COUNTER):06d}-{os.getpid()}", "w", encoding="utf-8") as handle:
                handle.write(f"{self.org_id}|{operation}")
        if not self.script and self.echo_delay is not None:
            self.script = [Delay(self.echo_delay, say_json(OUTPUT_FOR[operation.split(".", 1)[1]]))]
        return await super().chat(operation, request)


def main(argv):
    script = [_item(x) for x in json.loads(os.environ.get("WORKER_SCRIPT", "[]"))]
    echo = float(os.environ["ECHO_DELAY"]) if "ECHO_DELAY" in os.environ else None
    shared = RecordingGateway(None, echo, *script)

    def factory(db, org_id, user_id):
        shared.org_id = org_id
        return shared
    executor.GATEWAY_FACTORY = factory
    return cli(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
