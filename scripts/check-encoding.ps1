$ErrorActionPreference = "Stop"
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = $utf8NoBom
[Console]::OutputEncoding = $utf8NoBom

$textExtensions = @(
    ".py", ".ts", ".vue", ".css", ".md", ".txt", ".json", ".ps1", ".sh",
    ".html", ".yml", ".yaml", ".toml", ".ini", ".env", ".editorconfig",
    ".gitattributes"
)

$mojibakeChars = @(
    [char]0x6FEF,
    [char]0x93B5,
    [char]0x8DFA,
    [char]0x7EEF,
    [char]0x935B,
    [char]0x8FE9,
    [char]0x6960,
    [char]0x7487,
    [char]0x76E9,
    [char]0x4E36,
    [char]0xFFFD
)
$mojibakePattern = "[" + ([regex]::Escape((-join $mojibakeChars))) + "]"
$frontendChinesePattern = '[\p{IsCJKUnifiedIdeographs}]'
$issues = [System.Collections.Generic.List[string]]::new()

$files = @(
    git ls-files
    git ls-files --others --exclude-standard
) | Where-Object {
    $extension = [System.IO.Path]::GetExtension($_)
    $textExtensions -contains $extension -or $_ -in @(".editorconfig", ".gitattributes")
} | Sort-Object -Unique

foreach ($file in $files) {
    if (-not (Test-Path -LiteralPath $file)) {
        continue
    }

    $resolved = Resolve-Path -LiteralPath $file
    $bytes = [System.IO.File]::ReadAllBytes($resolved)
    if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
        $issues.Add("BOM`t$file")
    }

    $text = [System.Text.Encoding]::UTF8.GetString($bytes)
    $lf = ([regex]::Matches($text, "(?<!`r)`n")).Count
    $crlf = ([regex]::Matches($text, "`r`n")).Count
    if ($lf -gt 0 -and $crlf -gt 0) {
        $issues.Add("MIXED_EOL`t$file`tLF=$lf`tCRLF=$crlf")
    }

    if ($text -match $mojibakePattern) {
        $issues.Add("MOJIBAKE`t$file")
    }

    if ($file -like "frontend/src/*" -and $file -notlike "frontend/src/locales/*" -and $file -notmatch "\.test\.ts$") {
        $matches = Select-String -Path $file -Pattern $frontendChinesePattern -AllMatches
        foreach ($match in $matches) {
            $line = $match.Line.Trim()
            if ($line -match "^\s*(//|/\*|\*)") {
                continue
            }
            if ($line -match "includes\(") {
                continue
            }
            $issues.Add(("FRONTEND_LITERAL`t{0}:{1}`t{2}" -f $file, $match.LineNumber, $line))
        }
    }
}

if ($issues.Count -eq 0) {
    Write-Output "Encoding check passed."
    exit 0
}

$issues | Sort-Object -Unique | ForEach-Object { Write-Output $_ }
exit 1
