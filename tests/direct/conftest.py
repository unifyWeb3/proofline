"""Keep direct tests import-safe when the optional GenLayer suite is absent.

The current ``genlayer-test`` release registers its plugin through pytest's
entry point, so registering it here as well would duplicate the plugin.
"""

try:
    import gltest  # type: ignore  # noqa: F401
except ImportError:
    pass
else:
    # Keep the repository's direct-runner artifact in a durable workspace cache
    # so tests do not depend on a user's unrelated home cache state.
    from pathlib import Path

    from gltest.direct import sdk_loader

    _task_cache = Path(__file__).parents[2] / ".genvm-cache"
    if _task_cache.exists():
        sdk_loader.CACHE_DIR = _task_cache
        sdk_loader.BUNDLE_CACHE_DIR = _task_cache / "bundles-v2"
        sdk_loader.TREE_CACHE_DIR = _task_cache / "trees-v2"

    # genlayer-test 0.30.0rc2's loader targets the pre-RC top-level
    # ``genlayer.calldata`` and ``genlayer.types`` exports.  The matching RC
    # standard library exposes those modules under ``genlayer.py`` instead.
    # Keep this compatibility at the test boundary so the installed RC
    # packages remain untouched and the direct suite exercises the same
    # contract source as the hosted lane.
    from gltest.direct import loader as _direct_loader
    from gltest.direct import sdk_compat as _sdk_compat

    def _import_calldata():
        from genlayer.py import calldata

        return calldata

    def _import_types():
        from genlayer.py import types

        return types

    def _import_address():
        return _import_types().Address

    def _import_address_u256():
        sdk_types = _import_types()
        return sdk_types.Address, sdk_types.u256

    def _import_lazy():
        return _import_types().Lazy

    _sdk_compat.import_calldata = _import_calldata
    _sdk_compat.import_types = _import_types
    _sdk_compat.import_address = _import_address
    _sdk_compat.import_address_u256 = _import_address_u256
    _sdk_compat.import_lazy = _import_lazy
    from gltest.direct import wasi_mock as _wasi_mock

    _wasi_mock.import_calldata = _import_calldata
    _direct_loader.import_calldata = _import_calldata
    _direct_loader.import_address = _import_address
    _direct_loader.import_lazy = _import_lazy

    def _patch_run_nondet_rc():
        # The RC moved vm.py under genlayer.gl, while the test runner still
        # patches only the removed genlayer.vm import path.
        import genlayer.gl.vm as gl_vm
        from gltest.direct import wasi_mock

        if getattr(gl_vm, "_direct_mode_patched", False):
            return

        def _direct_run_nondet(leader_fn, validator_fn, /, **kwargs):
            vm = wasi_mock.get_vm()
            vm._in_nondet = True
            try:
                result = leader_fn()
                while isinstance(result, _import_lazy()):
                    result = result.get()
            finally:
                vm._in_nondet = False
            vm._captured_validators.append((result, leader_fn, validator_fn))
            return result

        def _direct_run_nondet_default(leader_fn, validator_fn, /, **kwargs):
            return _direct_run_nondet(leader_fn, validator_fn, **kwargs)

        Lazy = _import_lazy()

        def _lazy_run_nondet(leader_fn, validator_fn, /, **kwargs):
            return Lazy(lambda: _direct_run_nondet(leader_fn, validator_fn, **kwargs))

        def _lazy_run_nondet_default(leader_fn, validator_fn, /, **kwargs):
            return Lazy(
                lambda: _direct_run_nondet_default(leader_fn, validator_fn, **kwargs)
            )

        _direct_run_nondet.lazy = _lazy_run_nondet
        _direct_run_nondet_default.lazy = _lazy_run_nondet_default
        gl_vm.run_nondet = _direct_run_nondet
        gl_vm.run_nondet_default = _direct_run_nondet_default
        gl_vm.run_nondet_unsafe = _direct_run_nondet

        def _spawn_sandbox_rc(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        gl_vm.spawn_sandbox = _spawn_sandbox_rc
        gl_vm._direct_mode_patched = True

    _direct_loader._patch_run_nondet_for_direct_mode = _patch_run_nondet_rc

    _allocate_contract = _direct_loader._allocate_contract

    def _allocate_contract_rc(contract_cls, vm, *args, **kwargs):
        # The same RC layout moved storage from ``genlayer.storage`` to
        # ``genlayer.py.storage``.  The RC loader still imports the former
        # name when allocating a deployed contract.
        import importlib
        import sys

        storage = importlib.import_module("genlayer.py.storage")
        storage_internal = importlib.import_module("genlayer.py.storage._internal")
        storage_generate = importlib.import_module("genlayer.py.storage._internal.generate")
        sys.modules.setdefault("genlayer.storage", storage)
        sys.modules.setdefault("genlayer.storage._internal", storage_internal)
        sys.modules.setdefault("genlayer.storage._internal.generate", storage_generate)

        # The RC removed ``_BuilderCtx`` but the loader still imports it and
        # falls back to calling the original constructor without the storage
        # descriptor.  Allocate through the RC descriptor directly; its
        # generated constructor then sees the descriptor installed by ``td``.
        td = storage_generate._storage_build(contract_cls, {})
        slot = vm._storage.get_store_slot(storage.ROOT_SLOT_ID)
        instance = td.get(slot, 0)
        contract_cls.__init__(instance, *args, **kwargs)
        return instance

    _direct_loader._allocate_contract = _allocate_contract_rc

    # The RC message module materializes ``genlayer.gl.message`` as an
    # immutable NamedTuple.  genlayer-test 0.30.0rc2 still refreshes the
    # removed ``genlayer.message`` module, so sender changes after deploy are
    # otherwise invisible to the contract.
    from gltest.direct.vm import VMContext

    _refresh_gl_message = VMContext._refresh_gl_message

    def _refresh_gl_message_rc(vm):
        _refresh_gl_message(vm)
        gl_module = sys.modules.get("genlayer.gl")
        if gl_module is None:
            return

        Address = _import_address()

        def _address(value):
            if isinstance(value, Address):
                return value
            if isinstance(value, bytes):
                return Address(value)
            return Address(value.as_bytes)

        raw_module = sys.modules.get("genlayer._internal.msg")
        raw = getattr(raw_module, "message_raw", None)
        if not isinstance(raw, dict):
            return
        raw.update(
            {
                "sender_address": _address(vm.sender),
                "origin_address": _address(vm.origin),
                "value": vm.value,
                "chain_id": vm._chain_id,
            }
        )
        gl_module.message_raw = raw
        gl_module.message = gl_module.MessageType(
            contract_address=raw["contract_address"],
            sender_address=raw["sender_address"],
            origin_address=raw["origin_address"],
            value=gl_module.u256(raw["value"]),
            chain_id=gl_module.u256(raw["chain_id"]),
        )

    VMContext._refresh_gl_message = _refresh_gl_message_rc

    _run_validator_sentinel = __import__("gltest.direct.vm", fromlist=["_sentinel"])._sentinel

    def _run_validator_rc(self, *, leader_result=_run_validator_sentinel, leader_error=None, index=-1):
        if not self._captured_validators:
            raise RuntimeError(
                "No validator captured. Call a contract method that uses "
                "gl.vm.run_nondet before calling run_validator()."
            )
        stored_result, _leader_fn, validator_fn = self._captured_validators[index]
        import genlayer.gl.vm as gl_vm

        if leader_error is not None:
            wrapped = gl_vm.UserError(str(leader_error))
        elif leader_result is not _run_validator_sentinel:
            wrapped = gl_vm.Return(calldata=leader_result)
        else:
            wrapped = gl_vm.Return(calldata=stored_result)
        return validator_fn(wrapped)

    VMContext.run_validator = _run_validator_rc

import pytest
import sys

from proofline.fixtures import FIXTURE_TIMESTAMP


@pytest.fixture(autouse=True)
def _pin_direct_clock(direct_vm):
    """Keep deadline checks reproducible across wall-clock test runs."""

    direct_vm.warp(FIXTURE_TIMESTAMP)


@pytest.fixture(autouse=True)
def _reset_direct_contract_registry():
    """Reset the SDK's module-global single-contract guard per test."""

    module = sys.modules.get("genlayer.gl")
    if module is not None:
        module.genvm_contracts.__known_contract__ = None
    yield
    module = sys.modules.get("genlayer.gl")
    if module is not None:
        module.genvm_contracts.__known_contract__ = None
