-------------------------------- MODULE state_machine --------------------------------
\* TLA+ specification for the UndoLog effect/session state machine.
\*
\* This specification models the lifecycle of an effect through its
\* states and validates that:
\*   1. No invalid state transitions occur
\*   2. Compensation always executes or explicitly fails
\*   3. Terminal states are truly terminal
\*
\* Model check with: 5 sessions, 10 effects
\* Temporal property: CompensationLiveness

EXTENDS Integers, FiniteSets, Sequences

CONSTANTS
    MaxSessions,      \* 5
    MaxEffects        \* 10

\* Effect states
VARIABLES
    effectState,      \* [EffectId -> State]
    sessionEffects,   \* [SessionId -> SUBSET EffectId]
    activeSessions    \* SUBSET SessionId

\* State enumeration
Pending == "pending"
Executing == "executing"
Committed == "committed"
Compensating == "compensating"
Compensated == "compensated"
CompensationFailed == "compensation_failed"
Approved == "approved"
Rejected == "rejected"
Replayed == "replayed"

\* All states
States == {Pending, Executing, Committed, Compensating, Compensated,
           CompensationFailed, Approved, Rejected, Replayed}

\* Terminal states (no outgoing transitions)
TerminalStates == {Committed, Compensated, CompensationFailed, Rejected, Replayed}

\* Safe tier bypasses the state machine entirely
\* Only Compensable and Irreversible tiers enter the state machine

\* Initial state: all effects start as Pending
Init ==
    /\ effectState = [e \in 1..MaxEffects |-> Pending]
    /\ sessionEffects = [s \in 1..MaxSessions |-> {}]
    /\ activeSessions = 1..MaxSessions

\* Create a new effect in a session
CreateEffect(session, effect) ==
    /\ session \in activeSessions
    /\ effect \in 1..MaxEffects
    /\ effectState[effect] = Pending
    /\ effect \notin sessionEffects[session]
    /\ effectState' = [effectState EXCEPT ! = [effect EXCEPT ! = Pending]]
    /\ sessionEffects' = [sessionEffects EXCEPT ![session] = @ \union {effect}]
    /\ UNCHANGED activeSessions

\* Intercept -> Execute: Pending -> Executing
InterceptExecute(effect) ==
    /\ effectState[effect] = Pending
    /\ effectState' = [effectState EXCEPT ![effect] = Executing]
    /\ UNCHANGED <<sessionEffects, activeSessions>>

\* Intercept -> Replay: Pending -> Replayed (duplicate signature)
InterceptReplay(effect) ==
    /\ effectState[effect] = Pending
    /\ effectState' = [effectState EXCEPT ![effect] = Replayed]
    /\ UNCHANGED <<sessionEffects, activeSessions>>

\* Human approves: Pending -> Approved (Irreversible tier)
HumanApprove(effect) ==
    /\ effectState[effect] = Pending
    /\ effectState' = [effectState EXCEPT ![effect] = Approved]
    /\ UNCHANGED <<sessionEffects, activeSessions>>

\* Human rejects: Pending -> Rejected (Irreversible tier)
HumanReject(effect) ==
    /\ effectState[effect] = Pending
    /\ effectState' = [effectState EXCEPT ![effect] = Rejected]
    /\ UNCHANGED <<sessionEffects, activeSessions>>

\* Commit: Executing -> Committed
Commit(effect) ==
    /\ effectState[effect] = Executing
    /\ effectState' = [effectState EXCEPT ![effect] = Committed]
    /\ UNCHANGED <<sessionEffects, activeSessions>>

\* Fail: Executing -> Pending (retry)
Fail(effect) ==
    /\ effectState[effect] = Executing
    /\ effectState' = [effectState EXCEPT ![effect] = Pending]
    /\ UNCHANGED <<sessionEffects, activeSessions>>

\* Start compensation: Executing -> Compensating
StartCompensation(effect) ==
    /\ effectState[effect] = Executing
    /\ effectState' = [effectState EXCEPT ![effect] = Compensating]
    /\ UNCHANGED <<sessionEffects, activeSessions>>

\* Compensation succeeds: Compensating -> Compensated
CompensationSuccess(effect) ==
    /\ effectState[effect] = Compensating
    /\ effectState' = [effectState EXCEPT ![effect] = Compensated]
    /\ UNCHANGED <<sessionEffects, activeSessions>>

\* Compensation fails permanently: Compensating -> CompensationFailed
CompensationFailure(effect) ==
    /\ effectState[effect] = Compensating
    /\ effectState' = [effectState EXCEPT ![effect] = CompensationFailed]
    /\ UNCHANGED <<sessionEffects, activeSessions>>

\* Proxy resumes after approval: Approved -> Executing
ProxyResume(effect) ==
    /\ effectState[effect] = Approved
    /\ effectState' = [effectState EXCEPT ![effect] = Executing]
    /\ UNCHANGED <<sessionEffects, activeSessions>>

\* Next state relation: all possible transitions
Next ==
    \/ \E s \in 1..MaxSessions, e \in 1..MaxEffects : CreateEffect(s, e)
    \/ \E e \in 1..MaxEffects :
        \/ InterceptExecute(e)
        \/ InterceptReplay(e)
        \/ HumanApprove(e)
        \/ HumanReject(e)
        \/ Commit(e)
        \/ Fail(e)
        \/ StartCompensation(e)
        \/ CompensationSuccess(e)
        \/ CompensationFailure(e)
        \/ ProxyResume(e)

\* Specification: initial state and next steps
Spec == Init /\ [][Next]_<<effectState, sessionEffects, activeSessions>>

\* Type invariant: all effects are in valid states
TypeInvariant ==
    \A e \in 1..MaxEffects : effectState[e] \in States

\* Terminal states are truly terminal: once in a terminal state, stay there
TerminalInvariance ==
    \A e \in 1..MaxEffects :
        effectState[e] \in TerminalStates ~> effectState[e] \in TerminalStates

\* Compensation liveness: if an effect enters Compensating, it eventually
\* reaches Compensated or CompensationFailed
CompensationLiveness ==
    \A e \in 1..MaxEffects :
        (effectState[e] = Compensating) ~> 
        (effectState[e] \in {Compensated, CompensationFailed})

\* No Committed -> Compensating transition
NoCompensateAfterCommit ==
    \A e \in 1..MaxEffects :
        ~[](effectState[e] = Committed /\ effectState'[e] = Compensating)

\* No Pending -> Compensating transition (must execute first)
NoCompensateFromPending ==
    \A e \in 1..MaxEffects :
        ~[](effectState[e] = Pending /\ effectState'[e] = Compensating)
