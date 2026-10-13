"""执行传入的 pyOCD 配置；不解释板描述，不安装依赖。"""
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time


def select_probe(probes, requested=None):
    if requested:
        probes = [p for p in probes if requested.lower() in p.unique_id.lower()]
    if len(probes) != 1:
        raise RuntimeError(f"探针匹配数量为 {len(probes)}，请核对 debug.probe")
    return probes[0]


@contextlib.contextmanager
def probe_lock(identity, namespace, *, held=False):
    # 测试桥接可在整个生命周期持锁，内部操作显式复用同一把锁。
    if held:
        yield
        return
    # 沿用 MSP 的探针标识编码，namespace由config提供，可与旧工具共享锁。
    key = hashlib.sha256(identity.encode()).hexdigest()[:24]
    owner = "windows" if os.name == "nt" else str(os.getuid())
    file = Path(tempfile.gettempdir()) / f"{namespace}-{owner}-{key}.lock"
    with file.open("a+b") as handle:
        try:
            if os.name == "nt":
                import msvcrt
                if handle.seek(0, os.SEEK_END) == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError(f"探针正在被其他工具使用：{identity}") from exc
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def flash_segments(filename, memory):
    from elftools.elf.elffile import ELFFile
    segments = []
    start, end = memory["start"], memory["start"] + memory["size"]
    with Path(filename).open("rb") as handle:
        for segment in ELFFile(handle).iter_segments():
            if segment["p_type"] != "PT_LOAD" or not segment["p_filesz"]:
                continue
            address, size = segment["p_paddr"], segment["p_filesz"]
            if not start <= address < address + size <= end:
                raise ValueError(f"ELF 加载段超出配置的 Flash：0x{address:x}+0x{size:x}")
            segments.append((address, segment.data()))
    if not segments:
        raise ValueError("ELF 没有可下载的 Flash 加载段")
    return sorted(segments)


def verify_segments(target, segments, report, frequency):
    """读回真实加载段并保存证据，不改变目标运行状态。"""
    result = {"bytes": 0, "segments": []}
    for address, expected in segments:
        actual = bytearray()
        for offset in range(0, len(expected), 1024):
            actual.extend(target.read_memory_block8(
                address + offset, min(1024, len(expected) - offset)))
        (report / f"readback-{frequency}-{address:08x}.bin").write_bytes(actual)
        if actual != expected:
            raise RuntimeError(f"Flash 实读不一致：0x{address:08x}")
        result["segments"].append({"address": address, "size": len(expected)})
        result["bytes"] += len(expected)
    return result


def execute(config, operation, *, connector=None, programmer=None, segments_reader=None, lock_held=False):
    if operation not in ("flash", "reset", "probes", "readback"):
        raise ValueError(f"未知操作：{operation}")
    if operation == "readback":
        if config["debug"]["connect"] != "attach":
            raise ValueError("readback 必须使用 attach，不改变目标运行状态")
        if (config["pyocd"].get("cache.enable_memory") is not False
                or config["pyocd"].get("cache.read_code_from_elf") is not False):
            raise ValueError("readback 必须关闭内存与 ELF 读取缓存")
    # 支持注入后端以验证真实流程；只有实际执行才导入 pyOCD。
    if connector is None:
        from pyocd.core.helpers import ConnectHelper
        connector = ConnectHelper
    if operation == "probes":
        probes = connector.get_all_connected_probes(blocking=False)
        data = {"probes": [{"id": p.unique_id, "description": p.description} for p in probes]}
        print(json.dumps(data, ensure_ascii=False))
        return data
    if operation in ("flash", "readback"):
        if operation == "flash" and programmer is None:
            from pyocd.flash.file_programmer import FileProgrammer
            programmer = FileProgrammer
        segments = (segments_reader or flash_segments)(config["artifacts"]["elf"], config["memory"]["flash"])
    probe = select_probe(connector.get_all_connected_probes(blocking=False), config["debug"]["probe"])
    report = Path(config["report_dir"]) / (
        operation + "-" + time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns() % 1000000000:09d}")
    report.mkdir(parents=True)
    result = {"status": "FAIL", "chip": config["chip"], "probe": probe.unique_id, "attempts": []}
    pyocd_file = report / "pyocd.json"
    pyocd_file.write_text(json.dumps(config["pyocd"], ensure_ascii=False, indent=2) + "\n")
    frequencies = config["flash"]["frequencies"] if operation == "flash" else [config["debug"]["frequency"]]
    try:
        with probe_lock(probe.unique_id, config["lock_namespace"], held=lock_held):
            for frequency in frequencies:
                attempt = {"frequency": frequency, "status": "FAIL"}
                result["attempts"].append(attempt)
                try:
                    session = connector.session_with_chosen_probe(
                        unique_id=probe.unique_id, blocking=False,
                        connect_mode=config["debug"]["connect"], frequency=frequency,
                        resume_on_disconnect=operation != "readback",
                        config_file=str(pyocd_file), project_dir=config["project_root"])
                    if session is None:
                        raise RuntimeError("探针不可用")
                    with session:
                        if operation == "reset":
                            session.target.reset()
                        elif operation == "readback":
                            attempt.update(verify_segments(session.target, segments, report, frequency))
                            attempt["verified"] = True
                        else:
                            session.target.reset_and_halt()
                            programmer(session, chip_erase="sector").program(config["artifacts"]["elf"])
                            if config["flash"]["verify"]:
                                session.target.halt()
                                attempt.update(verify_segments(session.target, segments, report, frequency))
                            session.target.reset()
                            attempt.update(verified=config["flash"]["verify"], bytes=sum(len(data) for _, data in segments))
                    attempt["status"] = result["status"] = "PASS"
                    print(f"PASS: {operation}；{frequency} Hz；{report}", flush=True)
                    return result
                except Exception as exc:
                    attempt["error"] = str(exc)
                    print(f"{frequency} Hz 失败：{exc}", flush=True)
                finally:
                    (report / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        raise RuntimeError(f"全部配置频率执行失败，见 {report}")
    finally:
        (report / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--lock-held", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("operation", choices=("flash", "reset", "probes", "readback"))
    args = parser.parse_args()
    execute(json.loads(Path(args.config).read_text(encoding="utf-8-sig")), args.operation,
            lock_held=args.lock_held)


if __name__ == "__main__":
    main()
