//! Strongly-typed ID newtypes.
//!
//! Each domain entity gets its own ID type so the compiler prevents
//! passing an `OrgId` where a `SessionId` is expected.

use serde::{Deserialize, Serialize};
use std::fmt;
use uuid::Uuid;

macro_rules! define_id {
    ($name:ident, $doc:literal) => {
        #[doc = $doc]
        #[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
        #[serde(transparent)]
        pub struct $name(pub Uuid);

        impl $name {
            /// Create a new random ID (UUIDv4).
            #[must_use]
            pub fn new() -> Self {
                Self(Uuid::new_v4())
            }
            /// Wrap an existing UUID value.
            #[must_use]
            pub fn from_uuid(uuid: Uuid) -> Self {
                Self(uuid)
            }
            /// Borrow the underlying UUID.
            #[must_use]
            pub fn as_uuid(&self) -> &Uuid {
                &self.0
            }
        }

        impl Default for $name {
            fn default() -> Self {
                Self::new()
            }
        }

        impl fmt::Display for $name {
            fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                write!(f, "{}", self.0)
            }
        }

        impl From<Uuid> for $name {
            fn from(uuid: Uuid) -> Self {
                Self(uuid)
            }
        }

        impl From<$name> for Uuid {
            fn from(id: $name) -> Uuid {
                id.0
            }
        }

        impl std::str::FromStr for $name {
            type Err = uuid::Error;
            fn from_str(s: &str) -> Result<Self, Self::Err> {
                Ok(Self(s.parse()?))
            }
        }
    };
}

define_id!(OrgId, "Tenant organisation identifier");
define_id!(ProjectId, "Project identifier within an org");
define_id!(SessionId, "Agent execution session identifier");
define_id!(EffectId, "Individual tool-call effect record identifier");
define_id!(ToolId, "Tool registry entry identifier");
define_id!(CompensationId, "Compensation registry entry identifier");
define_id!(ApprovalRequestId, "Human-in-the-loop approval request identifier");
define_id!(ApprovalEventId, "Approval audit event identifier");
define_id!(UndoId, "Undo stack entry identifier");
define_id!(SnapshotId, "Session state snapshot identifier");

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ids_are_unique() {
        let a = SessionId::new();
        let b = SessionId::new();
        assert_ne!(a, b);
    }

    #[test]
    fn id_roundtrip_string() {
        let id = OrgId::new();
        let s = id.to_string();
        let parsed: OrgId = s.parse().unwrap();
        assert_eq!(id, parsed);
    }

    #[test]
    fn id_roundtrip_json() {
        let id = EffectId::new();
        let json = serde_json::to_string(&id).unwrap();
        let decoded: EffectId = serde_json::from_str(&json).unwrap();
        assert_eq!(id, decoded);
    }
}
