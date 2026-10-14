# Defer signed writes and encryption until Share, or until a mind leaves this machine

## Context and Problem Statement

The first plan gave each owner a keypair, signed every write to a mind, and encrypted each mind with its owner's key, so that what a mind holds could be attributed and tampering detected. Today every mind lives on one machine, under one account. Should Tapestry sign and encrypt now?

## Decision Drivers

* Security measures protect against something real.
* Work that isn't needed yet doesn't hold up a stage.

## Considered Options

* Sign writes and encrypt minds from the start
* Defer both until Share is built, or until a mind first lives off this machine

## Decision Outcome

Chosen option: "Defer both".

With every mind on one machine under one account, the private key would sit beside the file it protects. Signing would guard against almost nothing that file permissions don't already.

### Consequences

* Good, because the first stage is complete without key management.
* Bad, because minds written before signing exists will carry no signatures. Attribution for them rests on [evidence](../primitives/EVIDENCE.md) alone.

## More Information

Written on 2026-10-14, after the fact. Revisit when [Share](../capabilities/SHARE.md) is built, or when any mind is stored off this machine. That is when attribution and tamper evidence protect against something real.
