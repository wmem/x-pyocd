"""离线验证编程流程、Flash 边界和合作式锁，不连接硬件。"""
import copy
import hashlib
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

WORKER = Path(__file__).resolve().parents[1] / "addon/modules/runtime/worker.py"
spec = importlib.util.spec_from_file_location("x_pyocd_worker", WORKER)
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


def lock_child(identity, namespace, connection):
    try:
        with worker.probe_lock(identity, namespace):
            connection.send("acquired")
    except RuntimeError:
        connection.send("busy")
    finally:
        connection.close()


class FakeConnector:
    def __init__(self, segments, failures=()):
        self.segments = segments
        self.failures = failures
        self.events = []
        self.probes = [types.SimpleNamespace(unique_id="TEST-PROBE", description="offline")]

    def get_all_connected_probes(self, **kwargs):
        assert kwargs == {"blocking": False}
        return self.probes

    def session_with_chosen_probe(self, **kwargs):
        frequency = kwargs["frequency"]
        self.events.append(("connect", frequency, kwargs))
        connector = self

        class Session:
            def __enter__(self):
                connector.events.append(("enter", frequency))
                return self

            def __exit__(self, *exc):
                connector.events.append(("exit", frequency))

        class Target:
            def reset_and_halt(self):
                connector.events.append(("reset_and_halt", frequency))

            def halt(self):
                connector.events.append(("halt", frequency))

            def reset(self):
                connector.events.append(("reset", frequency))

            def read_memory_block8(self, address, size):
                connector.events.append(("read", frequency, address, size))
                for start, data in connector.segments:
                    if start <= address and address + size <= start + len(data):
                        output = list(data[address-start:address-start+size])
                        if frequency in connector.failures:
                            output[0] ^= 1
                        return output
                raise AssertionError("读回地址超出已下载段")

        session = Session()
        session.target = Target()
        session.frequency = frequency
        return session

    def programmer(self, session, **kwargs):
        assert kwargs == {"chip_erase": "sector"}
        self.events.append(("sector", session.frequency))
        connector = self

        class Programmer:
            def program(self, filename):
                connector.events.append(("program", session.frequency, filename))

        return Programmer()


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="x-pyocd-test-")
        self.addCleanup(self.temp.cleanup)
        self.config = {
            "host": "linux", "project_root": self.temp.name,
            "report_dir": str(Path(self.temp.name) / "reports"),
            "chip": "test-chip", "lock_namespace": "x-pyocd-tests",
            "debug": {"probe": "test-probe", "frequency": 100000, "connect": "halt", "protocol": "swd"},
            "pyocd": {"target_override": "test", "cache.enable_memory": False,
                      "cache.read_code_from_elf": False},
            "artifacts": {"elf": "test.elf"},
            "memory": {"flash": {"start": 0x08000000, "size": 0x10000}},
            "flash": {"verify": True, "frequencies": [4000000, 100000]},
        }
        self.segments = [(0x08000000, bytes(range(256)) * 9), (0x08003000, b"second segment")]

    def execute(self, connector, operation="flash"):
        return worker.execute(self.config, operation, connector=connector,
                              programmer=connector.programmer,
                              segments_reader=lambda *_: self.segments)

    def reports(self):
        return [json.loads(file.read_text()) for file in Path(self.config["report_dir"]).glob("*/result.json")]

    def test_flash_order_chunks_readback_and_config_isolation(self):
        before = copy.deepcopy(self.config)
        connector = FakeConnector(self.segments)
        result = self.execute(connector)
        names = [event[0] for event in connector.events]
        self.assertEqual(names, ["connect", "enter", "reset_and_halt", "sector", "program", "halt",
                                 "read", "read", "read", "read", "reset", "exit"])
        reads = [event[2:] for event in connector.events if event[0] == "read"]
        self.assertEqual(reads, [(0x08000000, 1024), (0x08000400, 1024), (0x08000800, 256),
                                (0x08003000, 14)])
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["attempts"][0]["bytes"], 2318)
        self.assertEqual(self.config, before)
        self.assertEqual(self.reports(), [result])
        directory = next(Path(self.config["report_dir"]).iterdir())
        for address, data in self.segments:
            self.assertEqual((directory / f"readback-4000000-{address:08x}.bin").read_bytes(), data)

    def test_failed_readback_retries_frequency_and_resets_only_success(self):
        connector = FakeConnector(self.segments, failures=[4000000])
        result = self.execute(connector)
        self.assertEqual([item["status"] for item in result["attempts"]], ["FAIL", "PASS"])
        self.assertIn("实读不一致", result["attempts"][0]["error"])
        self.assertEqual([event[1] for event in connector.events if event[0] == "reset"], [100000])
        self.assertEqual([event[1] for event in connector.events if event[0] == "exit"], [4000000, 100000])

    def test_all_readback_failures_save_report_and_release_lock(self):
        connector = FakeConnector(self.segments, failures=[4000000, 100000])
        with self.assertRaisesRegex(RuntimeError, "全部配置频率执行失败"):
            self.execute(connector)
        report = self.reports()[0]
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(len(report["attempts"]), 2)
        self.assertNotIn("reset", [event[0] for event in connector.events])
        with worker.probe_lock("TEST-PROBE", self.config["lock_namespace"]):
            pass

    def test_verify_false_programs_without_readback(self):
        self.config["flash"]["verify"] = False
        connector = FakeConnector(self.segments)
        result = self.execute(connector)
        self.assertNotIn("read", [event[0] for event in connector.events])
        self.assertFalse(result["attempts"][0]["verified"])

    def test_reset_does_not_read_or_program_elf(self):
        self.config.pop("artifacts")
        self.config.pop("flash")
        connector = FakeConnector([])
        self.assertEqual(self.execute(connector, "reset")["status"], "PASS")
        self.assertEqual([event[0] for event in connector.events], ["connect", "enter", "reset", "exit"])

    def test_readback_checks_segments_without_changing_target_state(self):
        self.config["debug"]["connect"] = "attach"
        self.config.pop("flash")
        connector = FakeConnector(self.segments)
        result = self.execute(connector, "readback")
        names = [event[0] for event in connector.events]
        self.assertEqual(names, ["connect", "enter", "read", "read", "read", "read", "exit"])
        self.assertFalse(connector.events[0][2]["resume_on_disconnect"])
        self.assertEqual(result["attempts"][0]["bytes"], 2318)
        self.assertTrue(result["attempts"][0]["verified"])
        self.assertEqual(self.reports(), [result])

    def test_readback_failure_never_programs_or_resets(self):
        self.config["debug"]["connect"] = "attach"
        connector = FakeConnector(self.segments, failures=[100000])
        with self.assertRaisesRegex(RuntimeError, "全部配置频率执行失败"):
            self.execute(connector, "readback")
        self.assertEqual(self.reports()[0]["status"], "FAIL")
        self.assertEqual([event[0] for event in connector.events],
                         ["connect", "enter", "read", "read", "read", "exit"])

    def test_readback_rejects_reset_connect_mode_and_caches_before_connect(self):
        connector = FakeConnector(self.segments)
        with self.assertRaisesRegex(ValueError, "attach"):
            self.execute(connector, "readback")
        self.config["debug"]["connect"] = "attach"
        for field in ("cache.enable_memory", "cache.read_code_from_elf"):
            with self.subTest(field=field):
                self.config["pyocd"][field] = True
                with self.assertRaisesRegex(ValueError, "缓存"):
                    self.execute(connector, "readback")
                self.config["pyocd"][field] = False
        self.assertEqual(connector.events, [])

    def test_probe_list_does_not_create_report(self):
        result = worker.execute({}, "probes", connector=FakeConnector([]))
        self.assertEqual(result["probes"][0]["id"], "TEST-PROBE")
        self.assertFalse(Path(self.config["report_dir"]).exists())

    def test_probe_selection_rejects_zero_and_multiple_matches(self):
        probes = [types.SimpleNamespace(unique_id="ABC1"), types.SimpleNamespace(unique_id="ABC2")]
        self.assertEqual(worker.select_probe(probes, "bc1").unique_id, "ABC1")
        for requested in ("absent", "abc", None):
            with self.assertRaises(RuntimeError):
                worker.select_probe(probes, requested)

    def test_connection_failure_report_and_fallback(self):
        connector = FakeConnector(self.segments)
        original = connector.session_with_chosen_probe
        connector.session_with_chosen_probe = lambda **kw: None if kw["frequency"] == 4000000 else original(**kw)
        result = self.execute(connector)
        self.assertEqual(result["attempts"][0]["error"], "探针不可用")
        self.assertEqual(result["status"], "PASS")

    def test_programming_failure_skips_readback_and_retries(self):
        connector = FakeConnector(self.segments)
        original = connector.programmer
        def programmer(session, **kwargs):
            if session.frequency == 4000000:
                class FailingProgrammer:
                    def program(self, _):
                        raise RuntimeError("测试扇区编程失败")
                return FailingProgrammer()
            return original(session, **kwargs)
        connector.programmer = programmer
        result = self.execute(connector)
        self.assertEqual(result["attempts"][0]["error"], "测试扇区编程失败")
        self.assertEqual(result["status"], "PASS")
        self.assertNotIn(4000000, [event[1] for event in connector.events if event[0] in ("read", "reset")])

    def test_elf_segments_sort_filter_and_boundaries(self):
        # 使用 elftools 协议替身验证本模块的边界逻辑；不声称验证 ELF 库本身。
        filename = Path(self.temp.name) / "sample.elf"
        filename.write_bytes(b"offline fixture")
        def segment(address, data, kind="PT_LOAD"):
            class Segment(dict):
                def data(self):
                    return data
            return Segment(p_type=kind, p_paddr=address, p_filesz=len(data))
        segments = [segment(0x08003000, b"b"), segment(0x08000000, b"a"),
                    segment(0, b"metadata", "PT_NOTE"), segment(0, b"")]
        elf = types.ModuleType("elftools.elf.elffile")
        elf.ELFFile = lambda _: types.SimpleNamespace(iter_segments=lambda: iter(segments))
        with patch.dict("sys.modules", {"elftools": types.ModuleType("elftools"),
                                       "elftools.elf": types.ModuleType("elftools.elf"),
                                       "elftools.elf.elffile": elf}):
            memory = self.config["memory"]["flash"]
            self.assertEqual(worker.flash_segments(filename, memory),
                             [(0x08000000, b"a"), (0x08003000, b"b")])
            for address, data in ((0x07ffffff, b"a"), (0x0800ffff, b"ab"), (0x08010000, b"a")):
                segments[:] = [segment(address, data)]
                with self.assertRaisesRegex(ValueError, "超出配置的 Flash"):
                    worker.flash_segments(filename, memory)
            segments[:] = [segment(0x0800ffff, b"a")]
            self.assertEqual(worker.flash_segments(filename, memory), [(0x0800ffff, b"a")])
            segments[:] = [segment(0, b"")]
            with self.assertRaisesRegex(ValueError, "没有可下载"):
                worker.flash_segments(filename, memory)

    @unittest.skipUnless(os.name == "posix", "此用例验证 Linux flock；Windows 锁需 Windows 环境")
    def test_legacy_lock_compatible_cross_process_and_exception_release(self):
        # 文件名编码与 MSP 一致；namespace=gd32 可与旧工具共享同一锁。
        import fcntl
        identity = "TEST-LOCK-COMPATIBILITY"
        key = hashlib.sha256(identity.encode()).hexdigest()[:24]
        filename = Path(tempfile.gettempdir()) / f"gd32-{os.getuid()}-{key}.lock"
        with filename.open("a+b") as legacy:
            fcntl.flock(legacy, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(RuntimeError, "其他工具"):
                with worker.probe_lock(identity, "gd32"):
                    self.fail("旧锁持有期间不应取得锁")
        ctx = multiprocessing.get_context("spawn")
        def child_result():
            parent, child = ctx.Pipe(duplex=False)
            process = ctx.Process(target=lock_child, args=(identity, "gd32", child))
            process.start()
            child.close()
            try:
                self.assertTrue(parent.poll(10), "锁测试子进程超时")
                result = parent.recv()
                process.join(10)
                self.assertEqual(process.exitcode, 0)
                return result
            finally:
                if process.is_alive():
                    process.terminate()
                    process.join()
                parent.close()
        with self.assertRaisesRegex(ValueError, "测试异常"):
            with worker.probe_lock(identity, "gd32"):
                self.assertEqual(child_result(), "busy")
                raise ValueError("测试异常")
        self.assertEqual(child_result(), "acquired")


if __name__ == "__main__":
    unittest.main(verbosity=2)
