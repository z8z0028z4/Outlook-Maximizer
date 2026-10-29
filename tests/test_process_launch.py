import os
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from outlook_maximizer import process_launch


class ProcessLaunchTests(unittest.TestCase):
    def setUp(self):
        if os.name != "nt":
            self.skipTest("Windows DLL search is Windows-specific")
        self.bundle = os.path.abspath(os.path.join(os.environ["TEMP"], "_MEI_test_launch"))
        self.frozen = patch.object(sys, "frozen", True, create=True)
        self.meipass = patch.object(sys, "_MEIPASS", self.bundle, create=True)
        self.frozen.start()
        self.meipass.start()
        self.addCleanup(self.frozen.stop)
        self.addCleanup(self.meipass.stop)

    def test_environment_removes_only_private_bundle_paths(self):
        inside = os.path.join(self.bundle, "PyQt6", "plugins")
        sibling = self.bundle + "_other"
        env = {
            "Path": os.pathsep.join((self.bundle, inside, sibling, r"C:\Windows\System32")),
            "QT_PLUGIN_PATH": inside,
            "QML2_IMPORT_PATH": os.pathsep.join((inside, sibling)),
            "_PYI_APPLICATION_HOME_DIR": self.bundle,
            "KEEP_ME": "value",
        }
        cleaned = process_launch._external_environment(env, self.bundle)
        self.assertEqual(cleaned["Path"], os.pathsep.join((sibling, r"C:\Windows\System32")))
        self.assertNotIn("QT_PLUGIN_PATH", cleaned)
        self.assertEqual(cleaned["QML2_IMPORT_PATH"], sibling)
        self.assertNotIn("_PYI_APPLICATION_HOME_DIR", cleaned)
        self.assertEqual(cleaned["KEEP_ME"], "value")
        self.assertIn("_PYI_APPLICATION_HOME_DIR", env)

    def test_windows_reports_empty_dll_directory_without_error(self):
        kernel = process_launch._kernel32()
        original = process_launch._get_dll_directory(kernel)
        try:
            process_launch._set_dll_directory(kernel, None)
            self.assertIsNone(process_launch._get_dll_directory(kernel))
        finally:
            process_launch._set_dll_directory(kernel, original)

    def test_spawn_restores_on_failure(self):
        with patch.object(process_launch, "_kernel32", return_value=object()), \
             patch.object(process_launch, "_get_dll_directory", return_value=self.bundle), \
             patch.object(process_launch, "_set_dll_directory") as set_dir, \
             patch.object(subprocess, "Popen", side_effect=OSError("failed")):
            with self.assertRaisesRegex(OSError, "failed"):
                process_launch.popen_external(["missing.exe"])
        self.assertEqual([call.args[1] for call in set_dir.call_args_list], [None, self.bundle])

    def test_concurrent_launches_are_serialized_and_restored(self):
        events = []
        start = threading.Barrier(3)
        failures = []

        def set_dir(_kernel, path):
            events.append((threading.current_thread().name, "restore" if path else "clear"))

        def spawn(*_args, **_kwargs):
            events.append((threading.current_thread().name, "spawn"))
            time.sleep(.02)
            return object()

        def worker():
            try:
                start.wait()
                process_launch.popen_external(["fake.exe"])
            except BaseException as exc:
                failures.append(exc)

        with patch.object(process_launch, "_kernel32", return_value=object()), \
             patch.object(process_launch, "_get_dll_directory", return_value=self.bundle), \
             patch.object(process_launch, "_set_dll_directory", side_effect=set_dir), \
             patch.object(subprocess, "Popen", side_effect=spawn):
            workers = [threading.Thread(target=worker, name=f"worker-{i}") for i in range(2)]
            for worker_thread in workers:
                worker_thread.start()
            start.wait()
            for worker_thread in workers:
                worker_thread.join(2)
        self.assertFalse(failures)
        self.assertTrue(all(not worker_thread.is_alive() for worker_thread in workers))
        self.assertEqual([event for _, event in events], ["clear", "spawn", "restore"] * 2)
        self.assertEqual(len({name for name, _ in events[:3]}), 1)
        self.assertEqual(len({name for name, _ in events[3:]}), 1)

    def test_run_waits_after_parent_search_path_is_restored(self):
        child = MagicMock()
        child.args = ["fake.exe"]
        child.__enter__.return_value = child
        child.communicate.return_value = ("ok", "")
        child.poll.return_value = 0
        with patch.object(process_launch, "_kernel32", return_value=object()), \
             patch.object(process_launch, "_get_dll_directory", return_value=self.bundle), \
             patch.object(process_launch, "_set_dll_directory") as set_dir, \
             patch.object(subprocess, "Popen", return_value=child):
            child.communicate.side_effect = lambda *_a, **_k: (
                self.assertEqual(set_dir.call_args.args[1], self.bundle) or "ok", ""
            )
            result = process_launch.run_external(["fake.exe"], text=True, capture_output=True)
        self.assertEqual(result.stdout, "ok")
        self.assertEqual(result.returncode, 0)

    def test_restoration_failure_stops_new_child(self):
        child = MagicMock()
        with patch.object(process_launch, "_kernel32", return_value=object()), \
             patch.object(process_launch, "_get_dll_directory", return_value=self.bundle), \
             patch.object(process_launch, "_set_dll_directory", side_effect=[None, OSError("restore")]), \
             patch.object(subprocess, "Popen", return_value=child):
            with self.assertRaisesRegex(OSError, "restore"):
                process_launch.popen_external(["fake.exe"])
        child.terminate.assert_called_once_with()
        child.wait.assert_called_once_with(timeout=3)


if __name__ == "__main__":
    unittest.main()
