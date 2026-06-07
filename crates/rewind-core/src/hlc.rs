//! Hybrid Logical Clock.
//!
//! IMPORTANT: the HLC provides a
//! canonical **total order + causality tag** for the event log. It does **NOT**
//! provide deterministic replay-scheduling. Concurrent replay determinism is a
//! separate, unbuilt mechanism (Spike-2). Do not conflate the two.
//!
//! Ord is lexicographic over (wall_ms, counter, node) — declaration order matters.

use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
pub struct Hlc {
    pub wall_ms: u64,
    pub counter: u32,
    pub node: u64,
}

impl Hlc {
    pub fn zero(node: u64) -> Self {
        Hlc {
            wall_ms: 0,
            counter: 0,
            node,
        }
    }

    /// Local event: advance using a physical clock reading (ms since epoch).
    pub fn tick(&mut self, physical_ms: u64) -> Hlc {
        if physical_ms > self.wall_ms {
            self.wall_ms = physical_ms;
            self.counter = 0;
        } else {
            self.counter = self.counter.saturating_add(1);
        }
        *self
    }

    /// Receive event: merge with a remote HLC (causality across tasks/processes).
    pub fn receive(&mut self, physical_ms: u64, remote: Hlc) -> Hlc {
        let max_wall = self.wall_ms.max(remote.wall_ms).max(physical_ms);
        if max_wall == self.wall_ms && max_wall == remote.wall_ms {
            self.counter = self.counter.max(remote.counter).saturating_add(1);
        } else if max_wall == self.wall_ms {
            self.counter = self.counter.saturating_add(1);
        } else if max_wall == remote.wall_ms {
            self.counter = remote.counter.saturating_add(1);
        } else {
            self.counter = 0;
        }
        self.wall_ms = max_wall;
        *self
    }
}
