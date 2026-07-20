param(
    [int]$TimeoutSeconds = 8,
    [ValidateSet("Command", "Hybrid", "Dictation")]
    [string]$Mode = "Hybrid"
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Speech

$resultPayload = @{
    ok = $false
    text = ""
    confidence = 0
    error = ""
}

try {
    $recognizer = New-Object System.Speech.Recognition.SpeechRecognitionEngine
    $recognizer.SetInputToDefaultAudioDevice()

    $commands = New-Object System.Speech.Recognition.Choices
    [void]$commands.Add("open assistant")
    [void]$commands.Add("hi")
    [void]$commands.Add("hello")
    [void]$commands.Add("what is your name")
    [void]$commands.Add("what's your name")
    [void]$commands.Add("what can you do")
    [void]$commands.Add("open dashboard")
    [void]$commands.Add("open tools")
    [void]$commands.Add("open projects")
    [void]$commands.Add("open codex")
    [void]$commands.Add("open tasks")
    [void]$commands.Add("open settings")
    [void]$commands.Add("read summary")
    [void]$commands.Add("test connections")
    [void]$commands.Add("pause escalations")
    [void]$commands.Add("show approvals")
    [void]$commands.Add("tool status")
    [void]$commands.Add("project status")
    [void]$commands.Add("connection status")
    [void]$commands.Add("google status")
    [void]$commands.Add("codex status")
    [void]$commands.Add("whatsapp status")
    [void]$commands.Add("weather update")
    [void]$commands.Add("weather in dhaka")
    [void]$commands.Add("weather in cumilla")
    [void]$commands.Add("weather in comilla")
    [void]$commands.Add("latest news")
    [void]$commands.Add("news in bangladesh")
    [void]$commands.Add("technology news")
    [void]$commands.Add("research wordpress plugin security")
    [void]$commands.Add("use edge voice")
    [void]$commands.Add("use windows voice")
    [void]$commands.Add("make voice faster")
    [void]$commands.Add("make voice slower")
    [void]$commands.Add("increase voice confidence")
    [void]$commands.Add("decrease voice confidence")
    [void]$commands.Add("set voice confidence to thirty")
    [void]$commands.Add("set voice confidence to forty")
    [void]$commands.Add("set voice confidence to fifty")
    [void]$commands.Add("set voice confidence to sixty")
    [void]$commands.Add("set confidence to thirty")
    [void]$commands.Add("set confidence to forty")
    [void]$commands.Add("set confidence to fifty")
    [void]$commands.Add("set confidence to sixty")
    [void]$commands.Add("set listen timeout to five")
    [void]$commands.Add("set listen timeout to eight")
    [void]$commands.Add("set listen timeout to ten")
    [void]$commands.Add("set listen timeout to fifteen")
    [void]$commands.Add("switch to dictation mode")
    [void]$commands.Add("switch to command mode")
    [void]$commands.Add("switch to hybrid mode")
    [void]$commands.Add("use productivity voice mode")

    $grammarBuilder = New-Object System.Speech.Recognition.GrammarBuilder
    $grammarBuilder.Append($commands)
    $commandGrammar = New-Object System.Speech.Recognition.Grammar($grammarBuilder)
    $recognizer.LoadGrammar($commandGrammar)

    if ($Mode -eq "Hybrid") {
        $prefixes = @(
            "create todo",
            "add todo",
            "create task",
            "add task",
            "new todo",
            "remind me to",
            "create reminder to",
            "add reminder to",
            "schedule meeting",
            "schedule event",
            "create meeting",
            "create event",
            "research",
            "search"
        )
        foreach ($prefix in $prefixes) {
            try {
                $builder = New-Object System.Speech.Recognition.GrammarBuilder
                $builder.Append($prefix)
                $builder.AppendDictation()
                $grammar = New-Object System.Speech.Recognition.Grammar($builder)
                $recognizer.LoadGrammar($grammar)
            } catch {
                # Some Windows speech installations do not support constrained dictation.
            }
        }
    }

    if ($Mode -eq "Dictation") {
        $dictation = New-Object System.Speech.Recognition.DictationGrammar
        $recognizer.LoadGrammar($dictation)
    }

    $result = $recognizer.Recognize([TimeSpan]::FromSeconds($TimeoutSeconds))
    if ($null -ne $result) {
        $resultPayload.ok = $true
        $resultPayload.text = $result.Text
        $resultPayload.confidence = [Math]::Round($result.Confidence, 3)
    }
} catch {
    $resultPayload.error = $_.Exception.Message
}

$resultPayload | ConvertTo-Json -Depth 4
