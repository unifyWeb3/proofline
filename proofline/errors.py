"""Errors raised by the Proofline domain and validation layers."""


class ProoflineError(ValueError):
    """Base class for malformed or policy-invalid Proofline input."""


class SchemaError(ProoflineError):
    """Input does not conform to a supported versioned schema."""


class SignatureError(ProoflineError):
    """A signature is absent, malformed, or does not recover the signer."""


class ReceiptError(ProoflineError):
    """A receipt is malformed or cannot be consumed by an adapter."""


class ProtocolError(RuntimeError):
    """A GenLayer/evaluator/RPC failure, never an application verdict."""
