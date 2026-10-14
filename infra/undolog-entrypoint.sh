#!/bin/sh
# Supervising entrypoint for the combined UndoLog container (ADR 0021).
#
# Starts the engine and the proxy, forwards SIGTERM and SIGINT to both
# so each graceful shutdown path runs, and exits when either process
# exits: a container running one half of the pair is the half-running
# state the runtime must not keep, so the restart policy brings both
# processes back together.
set -eu

echo "undolog-entrypoint: starting engine and proxy"

undolog-engine &
engine_pid=$!
undolog-proxy &
proxy_pid=$!

# Installed as soon as both children exist; an orchestrator signals the
# container long after this window closes.
forward_signal() {
  trap - TERM INT
  echo "undolog-entrypoint: forwarding SIGTERM to engine and proxy"
  kill -TERM "$engine_pid" "$proxy_pid" 2>/dev/null || true
}
trap forward_signal TERM INT

# Poll both children so the exit of either one is noticed. The sleep
# runs in the background so a trapped signal interrupts the wait at
# once instead of after the next interval.
while kill -0 "$engine_pid" 2>/dev/null && kill -0 "$proxy_pid" 2>/dev/null; do
  sleep 1 &
  wait "$!" || true
done

# One child has exited. Identify it, stop the other so the pair never
# runs half-done, and reap both so no zombie outlives the container.
if kill -0 "$engine_pid" 2>/dev/null; then
  first_name=proxy
  first_pid=$proxy_pid
  other_name=engine
  other_pid=$engine_pid
else
  first_name=engine
  first_pid=$engine_pid
  other_name=proxy
  other_pid=$proxy_pid
fi

first_status=0
wait "$first_pid" || first_status=$?
echo "undolog-entrypoint: $first_name exited with status $first_status"

kill -TERM "$other_pid" 2>/dev/null || true
other_status=0
wait "$other_pid" || other_status=$?
echo "undolog-entrypoint: $other_name exited with status $other_status"

# The first exit decides the container status: a crash takes the whole
# unit down so the restart policy starts both processes again.
exit "$first_status"
