from __future__ import annotations

import ctypes
import json
import logging
from pathlib import Path

from ..config import settings
from ..utils import file_sha256

log = logging.getLogger(__name__)

LIBRARY = "/opt/rocm/lib/libmigraphx_c.so"
MANIFEST = "manifest.json"
_cache: dict[str, object] = {}
_warned: set[str] = set()


class MigraphxUnavailable(RuntimeError):
    pass


def _bind(lib, name, argtypes):
    fn = getattr(lib, name)
    fn.argtypes = argtypes
    fn.restype = ctypes.c_int
    return fn


class _Api:
    def __init__(self) -> None:
        pointer = ctypes.c_void_p
        lib = ctypes.CDLL(LIBRARY)
        self.file_options = _bind(lib, "migraphx_file_options_create",
                                  [ctypes.POINTER(pointer)])
        self.load = _bind(lib, "migraphx_load",
                          [ctypes.POINTER(pointer), ctypes.c_char_p, pointer])
        self.param_shapes = _bind(lib, "migraphx_program_get_parameter_shapes",
                                  [ctypes.POINTER(pointer), pointer])
        self.param_get = _bind(lib, "migraphx_program_parameter_shapes_get",
                               [ctypes.POINTER(pointer), pointer, ctypes.c_char_p])
        self.argument = _bind(lib, "migraphx_argument_create",
                              [ctypes.POINTER(pointer), pointer, pointer])
        self.params_create = _bind(lib, "migraphx_program_parameters_create",
                                   [ctypes.POINTER(pointer)])
        self.params_add = _bind(lib, "migraphx_program_parameters_add",
                                [pointer, ctypes.c_char_p, pointer])
        self.run = _bind(lib, "migraphx_program_run",
                         [ctypes.POINTER(pointer), pointer, pointer])
        self.outputs_size = _bind(lib, "migraphx_arguments_size",
                                  [ctypes.POINTER(ctypes.c_size_t), pointer])
        self.outputs_get = _bind(lib, "migraphx_arguments_get",
                                 [ctypes.POINTER(pointer), pointer, ctypes.c_size_t])
        self.argument_shape = _bind(lib, "migraphx_argument_shape",
                                    [ctypes.POINTER(pointer), pointer])
        self.argument_buffer = _bind(lib, "migraphx_argument_buffer",
                                     [ctypes.POINTER(ctypes.c_char_p), pointer])
        self.shape_lengths = _bind(
            lib, "migraphx_shape_lengths",
            [ctypes.POINTER(ctypes.POINTER(ctypes.c_size_t)),
             ctypes.POINTER(ctypes.c_size_t), pointer])
        self.shape_bytes = _bind(lib, "migraphx_shape_bytes",
                                 [ctypes.POINTER(ctypes.c_size_t), pointer])


_api: _Api | None = None


def _api_handle() -> _Api:
    global _api
    if _api is None:
        if not Path(LIBRARY).is_file():
            raise MigraphxUnavailable(f"MIGraphX kitaplığı yok: {LIBRARY}")
        _api = _Api()
    return _api


def _check(status: int, step: str) -> None:
    if status:
        raise MigraphxUnavailable(f"MIGraphX {step} başarısız: {status}")


class GpuSession:
    def __init__(self, artifact: Path, input_name: str = "pixel_values") -> None:
        api = _api_handle()
        pointer = ctypes.c_void_p
        options = pointer()
        _check(api.file_options(ctypes.byref(options)), "file_options")
        self._program = pointer()
        _check(api.load(ctypes.byref(self._program), str(artifact).encode(), options),
               "load")
        shapes = pointer()
        _check(api.param_shapes(ctypes.byref(shapes), self._program), "param_shapes")
        self._shape = pointer()
        _check(api.param_get(ctypes.byref(self._shape), shapes, input_name.encode()),
               "param_get")
        lengths = ctypes.POINTER(ctypes.c_size_t)()
        rank = ctypes.c_size_t()
        _check(api.shape_lengths(ctypes.byref(lengths), ctypes.byref(rank), self._shape),
               "shape_lengths")
        self.input_shape = tuple(lengths[i] for i in range(rank.value))
        self.batch = self.input_shape[0]
        self._input_name = input_name.encode()
        self._api = api
        self.artifact = artifact

    def _run_fixed(self, block):
        import numpy as np

        pointer = ctypes.c_void_p
        api = self._api
        block = np.ascontiguousarray(block, dtype=np.float32)
        argument = pointer()
        _check(api.argument(ctypes.byref(argument), self._shape,
                            ctypes.c_void_p(block.ctypes.data)), "argument")
        params = pointer()
        _check(api.params_create(ctypes.byref(params)), "params_create")
        _check(api.params_add(params, self._input_name, argument), "params_add")
        outputs = pointer()
        _check(api.run(ctypes.byref(outputs), self._program, params), "run")
        count = ctypes.c_size_t()
        _check(api.outputs_size(ctypes.byref(count), outputs), "outputs_size")
        result = []
        for index in range(count.value):
            item = pointer()
            _check(api.outputs_get(ctypes.byref(item), outputs, index), "outputs_get")
            shape = pointer()
            _check(api.argument_shape(ctypes.byref(shape), item), "argument_shape")
            lengths = ctypes.POINTER(ctypes.c_size_t)()
            rank = ctypes.c_size_t()
            _check(api.shape_lengths(ctypes.byref(lengths), ctypes.byref(rank), shape),
                   "output_lengths")
            size = ctypes.c_size_t()
            _check(api.shape_bytes(ctypes.byref(size), shape), "output_bytes")
            buffer = ctypes.c_char_p()
            _check(api.argument_buffer(ctypes.byref(buffer), item), "output_buffer")
            dims = tuple(lengths[i] for i in range(rank.value))
            raw = ctypes.string_at(buffer, size.value)
            result.append(np.frombuffer(raw, dtype=np.float32).reshape(dims).copy())
        return result

    def run(self, _output_names, feeds):
        import numpy as np

        data = np.ascontiguousarray(next(iter(feeds.values())), dtype=np.float32)
        if data.shape[1:] != self.input_shape[1:]:
            raise MigraphxUnavailable(
                f"girdi şekli uyumsuz: {data.shape} != {self.input_shape}")
        total = data.shape[0]
        chunks = []
        for offset in range(0, total, self.batch):
            block = data[offset:offset + self.batch]
            if block.shape[0] < self.batch:
                pad = np.zeros((self.batch - block.shape[0], *block.shape[1:]),
                               dtype=np.float32)
                block = np.concatenate([block, pad], axis=0)
            chunks.append(self._run_fixed(block))
        merged = []
        for position in range(len(chunks[0])):
            stacked = np.concatenate([chunk[position] for chunk in chunks], axis=0)
            merged.append(stacked[:total])
        return merged


def _artifact_dir() -> Path | None:
    configured = settings.migraphx_dir.strip()
    if not configured:
        return None
    root = Path(configured).expanduser()
    return root if root.is_dir() else None


def load(role: str, source_onnx: Path) -> GpuSession | None:
    root = _artifact_dir()
    if root is None:
        return None
    key = f"{role}:{source_onnx}"
    cached = _cache.get(key)
    if cached is not None:
        return cached if isinstance(cached, GpuSession) else None
    artifact = root / f"{role}.mxr"
    manifest_path = root / MANIFEST
    try:
        if not artifact.is_file():
            raise MigraphxUnavailable(f"derlenmiş artifact yok: {artifact}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        entry = manifest.get(role)
        if not isinstance(entry, dict):
            raise MigraphxUnavailable(f"manifest {role} kaydı yok")
        if entry.get("source_sha256") != file_sha256(source_onnx):
            raise MigraphxUnavailable(f"{role} artifact kaynağı değişmiş")
        session = GpuSession(artifact)
    except Exception as exc:
        _cache[key] = False
        if key not in _warned:
            _warned.add(key)
            log.warning("MIGraphX %s kullanılmıyor, CPU sürüyor: %s", role, exc)
        return None
    _cache[key] = session
    log.info("MIGraphX %s etkin: %s batch=%d", role, artifact.name, session.batch)
    return session


def reset_cache() -> None:
    _cache.clear()
    _warned.clear()


__all__ = ["GpuSession", "MigraphxUnavailable", "load", "reset_cache"]
