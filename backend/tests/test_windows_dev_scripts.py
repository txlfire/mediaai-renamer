"""Windows开发脚本的语法及进程归属回归，不启动或终止真实服务。"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(os.name == "nt" and shutil.which("powershell.exe"), "需要Windows PowerShell")
class WindowsDevScriptsTest(unittest.TestCase):
    root = Path(__file__).resolve().parents[2]

    def _run(self, script):
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                                 "$ErrorActionPreference='Stop';" + script],
                                capture_output=True, text=True, timeout=25)
        self.assertEqual(0, result.returncode, result.stderr)
        return json.loads(result.stdout)

    def test_native_powershell_parses_both_scripts_without_executing_them(self):
        root = str(self.root).replace("'", "''")
        result = self._run(f"""
            $failures=@(); foreach($name in @('start-dev-lan.ps1','stop-dev-lan.ps1')) {{
                $errors=$null; $tokens=$null;
                [System.Management.Automation.Language.Parser]::ParseFile((Join-Path '{root}' ('scripts/'+$name)),[ref]$tokens,[ref]$errors) | Out-Null;
                $failures += @($errors | ForEach-Object {{ $_.Message }});
            }}
            ConvertTo-Json -InputObject @($failures) -Compress
        """)
        self.assertEqual([], result)

    def test_stop_targets_descendants_and_rejects_foreign_or_reused_pid(self):
        root = str(self.root).replace("'", "''")
        for reused in (False, True):
            with self.subTest(reused=reused):
                offset = "5" if reused else "-1"
                result = self._run(rf"""
                    $errors=$null; $tokens=$null;
                    $ast=[System.Management.Automation.Language.Parser]::ParseFile((Join-Path '{root}' 'scripts/stop-dev-lan.ps1'),[ref]$tokens,[ref]$errors);
                    $fn=$ast.FindAll({{param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Stop-ProjectDevProcesses'}},$true)[0];
                    Invoke-Expression $fn.Extent.Text;
                    $stamp=[DateTime]::UtcNow;
                    $script:fixture=@(
                        [pscustomobject]@{{ProcessId=101;ParentProcessId=1;Name='python.exe';CommandLine='{root}\.venv\Scripts\python.exe -m uvicorn app.main:app';CreationDate=$stamp.AddSeconds(-3)}},
                        [pscustomobject]@{{ProcessId=102;ParentProcessId=101;Name='python.exe';CommandLine='multiprocessing';CreationDate=$stamp.AddSeconds(-2)}},
                        [pscustomobject]@{{ProcessId=103;ParentProcessId=102;Name='python.exe';CommandLine='worker';CreationDate=$stamp.AddSeconds(-1)}},
                        [pscustomobject]@{{ProcessId=201;ParentProcessId=1;Name='python.exe';CommandLine='C:\another-project\python.exe -m uvicorn app.main:app --port 8970';CreationDate=$stamp.AddSeconds(-3)}},
                        [pscustomobject]@{{ProcessId=301;ParentProcessId=1;Name='node.exe';CommandLine='node.exe node_modules\vite\bin\vite.js --config frontend/vite.config.ts';CreationDate=$stamp.AddSeconds({offset})}},
                        [pscustomobject]@{{ProcessId=401;ParentProcessId=1;Name='pwsh.exe';CommandLine='inspect {root} uvicorn app.main:app';CreationDate=$stamp.AddSeconds(-3)}}
                    );
                    $script:stopped=@();
                    function Get-CimInstance {{param($ClassName,$ErrorAction) return $script:fixture}}
                    function Test-Path {{param($LiteralPath) return $LiteralPath.EndsWith('frontend.pid')}}
                    function Get-Content {{param($LiteralPath,$ErrorAction) return '301'}}
                    function Get-Item {{param($LiteralPath) return [pscustomobject]@{{LastWriteTimeUtc=$stamp}}}}
                    function Stop-Process {{param($Id,[switch]$Force,$ErrorAction) $script:stopped += [int]$Id}}
                    Stop-ProjectDevProcesses -RootPath '{root}' -LogPath 'synthetic-log-directory';
                    ConvertTo-Json -InputObject @($script:stopped) -Compress
                """)
                self.assertEqual({101, 102, 103} if reused else {101, 102, 103, 301}, set(result))
                self.assertLess(result.index(103), result.index(102))
                self.assertLess(result.index(102), result.index(101))
