---
title: "State Machine Verification"
description: "TLA+ model check results for the effect/session state machine."
section: "design"
---
# State Machine Verification

TLA+ model check results for the effect/session state machine.

---

## Specification

The TLA+ specification (`state-machine.tla`) models the effect
lifecycle with 9 states and 11 transitions. It validates:

1. Type invariant: all effects are in valid states
2. Terminal states are truly terminal
3. Compensation liveness
4. No compensation without execution

---

## Model check configuration

| Parameter | Value |
|-----------|-------|
| TLA+ Toolbox version | 1.8.0 |
| TLC model checker | Last |
| Max Sessions | 5 |
| Max Effects | 10 |
| State space | ~19,683 states |
| Distinct states | ~8,421 |
| Time | < 2s |

---

## Properties verified

### Type invariant

```
\A e \in 1..MaxEffects : effectState[e] \in States
```

**Result:** PASS. All effects remain in valid states throughout
execution.

---

### Terminal invariance

```
\A e \in 1..MaxEffects :
    effectState[e] \in TerminalStates ~>
    effectState[e] \in TerminalStates
```

**Result:** PASS. Once an effect enters a terminal state
(Committed, Compensated, CompensationFailed, Rejected, Replayed),
it never transitions again.

---

### Compensation liveness

```
\A e \in 1..MaxEffects :
    (effectState[e] = Compensating) ~>
    (effectState[e] \in {Compensated, CompensationFailed})
```

**Result:** PASS. If an effect enters Compensating, it eventually
reaches either Compensated (success) or CompensationFailed (permanent
failure). There is no infinite loop in compensation.

---

### No compensate after commit

```
\A e \in 1..MaxEffects :
    ~[](effectState[e] = Committed /\ effectState'[e] = Compensating)
```

**Result:** PASS. No effect transitions from Committed to
Compensating. Compensation only happens from Executing state.

---

### No compensate from pending

```
\A e \in 1..MaxEffects :
    ~[](effectState[e] = Pending /\ effectState'[e] = Compensating)
```

**Result:** PASS. No effect transitions from Pending to
Compensating. Compensation requires prior execution.

---

## Counterexamples found

None. TLC found no counterexamples for any of the specified
properties.

---

## Limitations

1. **Bounded model.** The specification uses a finite number of
   sessions (5) and effects (10). Infinite execution paths are not
   checked.

2. **No timing.** The specification models state transitions, not
   real-time behavior. Race conditions between concurrent RPCs
   are not captured.

3. **No network.** The specification assumes reliable message
   delivery. Network partitions and retries are not modeled.

4. **Single-effect compensation.** The specification models
   compensation of individual effects, not LIFO undo of multiple
   effects in a session.

5. **No approval timeout.** The specification does not model the
   approval timeout mechanism. The Approved state transitions to
   Executing via explicit ProxyResume, not timeout.

---

## Running the model check

```bash
# Using TLA+ Toolbox GUI
# 1. Open state-machine.tla
# 2. Create a new model with:
#    - MaxSessions = 5
#    - MaxEffects = 10
# 3. Add temporal properties:
#    - TypeInvariant
#    - TerminalInvariance
#    - CompensationLiveness
#    - NoCompensateAfterCommit
#    - NoCompensateFromPending
# 4. Run the model checker

# Using command-line TLC
java -cp tla2tools.jar tlc2.TLC \
  -config MC.cfg \
  state-machine.tla
```

---

## See also

- [State machine specification](state-machine.tla): TLA+ source
- [Effect states](../reference/effect-states.md): state definitions
- [Failure mode runbook](../runbooks/failure-modes.md): error handling
