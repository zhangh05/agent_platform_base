# Memory Subsystem

The base memory system separates raw experience, durable user preferences, reusable facts, cases, and procedures.

## Flow

```text
completed turn
  -> append experience journal
  -> task-boundary reflection
  -> MemoryWriteGate
  -> MemoryStore
  -> ContextStore retrieval index
```

Retrieved memory enters the runtime as bounded `data_only` context. It can inform answers, but it cannot override system instructions or tool policy.
