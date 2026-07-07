using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Net.Http;
using System.Reflection;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace MercadoDiscountManagerStandalone;

internal static class Program
{
    private const int Port = 28758;
    private const string Url = "http://127.0.0.1:28758";
    private const string HealthUrl = "http://127.0.0.1:28758/api/health";
    private const string AuthDir = @"C:\Users\dztf6\Documents\美客多授权";
    private static readonly object ServiceLogLock = new();

    [STAThread]
    private static int Main(string[] args)
    {
        var noOpen = Array.Exists(args, arg => string.Equals(arg, "--no-open", StringComparison.OrdinalIgnoreCase));
        Process? startedService = null;
        Task<Process?>? serviceWarmupTask = null;

        try
        {
            ApplicationConfiguration.Initialize();
            if (noOpen)
            {
                startedService = EnsureServiceAsync().GetAwaiter().GetResult();
                return 0;
            }

            serviceWarmupTask = Task.Run(EnsureServiceAsync);
            Application.Run(new MainForm(startedService, serviceWarmupTask));
            return 0;
        }
        catch (Exception ex)
        {
            try
            {
                MessageBox.Show(ex.Message, "美客多折扣管家", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
            catch
            {
                // WinExe has no console fallback.
            }
            return 1;
        }
    }

    private static async Task<Process?> EnsureServiceAsync()
    {
        var root = GetInstallRoot();
        var appDir = Path.Combine(root, "app");
        var dataDir = Path.Combine(root, "data");
        var logDir = Path.Combine(dataDir, "logs");
        var stdoutLog = Path.Combine(logDir, "server.out.log");
        var stderrLog = Path.Combine(logDir, "server.err.log");

        if (await IsHealthy())
        {
            return null;
        }

        var portOwner = GetPortOwnerPid();
        if (portOwner.HasValue)
        {
            if (IsOwnNodeService(portOwner.Value, root))
            {
                StopProcessTree(portOwner.Value);
                await Task.Delay(800);
            }
            else
            {
                throw new InvalidOperationException(
                    $"本地服务端口 {Port} 已被其它程序占用，折扣管家无法启动内置服务。\r\n占用进程：{DescribeProcess(portOwner.Value)}\r\n请关闭占用程序后重新打开。");
            }
        }

        Directory.CreateDirectory(root);
        Directory.CreateDirectory(dataDir);
        Directory.CreateDirectory(logDir);
        ExtractPayload(root);

        var nodeExe = Path.Combine(root, "node", "node.exe");
        var serverJs = Path.Combine(appDir, "src", "server.js");
        if (!File.Exists(nodeExe)) throw new FileNotFoundException("完整包中缺少 Node.exe。", nodeExe);
        if (!File.Exists(serverJs)) throw new FileNotFoundException("完整包中缺少 server.js。", serverJs);

        File.AppendAllText(stdoutLog, $"{DateTime.Now:yyyy-MM-dd HH:mm:ss} starting service: {nodeExe} src/server.js{Environment.NewLine}", Encoding.UTF8);

        var psi = new ProcessStartInfo
        {
            FileName = nodeExe,
            Arguments = "src/server.js",
            WorkingDirectory = appDir,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8
        };
        psi.Environment["MDM_DATA_DIR"] = dataDir;
        psi.Environment["ML_STANDALONE_AUTH_DIR"] = AuthDir;

        var process = Process.Start(psi)
            ?? throw new InvalidOperationException("启动内置服务失败。");
        AttachServiceLogs(process, stdoutLog, stderrLog);

        for (var i = 0; i < 60; i++)
        {
            if (await IsHealthy()) return process;
            if (process.HasExited)
            {
                throw new InvalidOperationException(
                    $"内置服务启动后已退出，退出码 {process.ExitCode}。\r\n日志目录：{logDir}\r\n错误摘要：{ReadTail(stderrLog)}");
            }
            await Task.Delay(500);
        }

        throw new TimeoutException($"服务启动超时。\r\n端口：{Port}\r\n数据目录：{dataDir}\r\n日志目录：{logDir}\r\n端口占用：{DescribeCurrentPortOwner()}");
    }

    private static void AttachServiceLogs(Process process, string stdoutLog, string stderrLog)
    {
        process.OutputDataReceived += (_, e) =>
        {
            if (!string.IsNullOrEmpty(e.Data)) AppendServiceLog(stdoutLog, e.Data);
        };
        process.ErrorDataReceived += (_, e) =>
        {
            if (!string.IsNullOrEmpty(e.Data)) AppendServiceLog(stderrLog, e.Data);
        };
        process.BeginOutputReadLine();
        process.BeginErrorReadLine();
    }

    private static void AppendServiceLog(string path, string line)
    {
        lock (ServiceLogLock)
        {
            File.AppendAllText(path, $"{DateTime.Now:yyyy-MM-dd HH:mm:ss} {line}{Environment.NewLine}", Encoding.UTF8);
        }
    }

    private static int? GetPortOwnerPid()
    {
        try
        {
            using var process = new Process();
            process.StartInfo = new ProcessStartInfo
            {
                FileName = "netstat.exe",
                Arguments = "-ano -p tcp",
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8,
                StandardErrorEncoding = Encoding.UTF8
            };
            process.Start();
            var output = process.StandardOutput.ReadToEnd();
            process.WaitForExit(5000);
            foreach (var line in output.Split(new[] { '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries))
            {
                var parts = line.Split(' ', StringSplitOptions.RemoveEmptyEntries);
                if (parts.Length < 5) continue;
                if (!parts[0].Equals("TCP", StringComparison.OrdinalIgnoreCase)) continue;
                if (!parts[3].Equals("LISTENING", StringComparison.OrdinalIgnoreCase)) continue;
                if (!parts[1].EndsWith($":{Port}", StringComparison.OrdinalIgnoreCase)) continue;
                if (int.TryParse(parts[4], out var pid)) return pid;
            }
        }
        catch
        {
            // Port-owner detection is best effort; startup health checks still guard correctness.
        }
        return null;
    }

    private static bool IsOwnNodeService(int pid, string root)
    {
        try
        {
            using var process = Process.GetProcessById(pid);
            var path = process.MainModule?.FileName ?? "";
            var expectedNodeDir = Path.Combine(root, "node");
            return path.StartsWith(expectedNodeDir, StringComparison.OrdinalIgnoreCase);
        }
        catch
        {
            return false;
        }
    }

    private static void StopProcessTree(int pid)
    {
        try
        {
            using var process = Process.GetProcessById(pid);
            process.Kill(entireProcessTree: true);
            process.WaitForExit(3000);
        }
        catch
        {
            // If it has already exited, the next health/startup check will decide what to do.
        }
    }

    private static string DescribeCurrentPortOwner()
    {
        var owner = GetPortOwnerPid();
        return owner.HasValue ? DescribeProcess(owner.Value) : "未发现监听进程";
    }

    private static string DescribeProcess(int pid)
    {
        try
        {
            using var process = Process.GetProcessById(pid);
            var path = "";
            try { path = process.MainModule?.FileName ?? ""; } catch { }
            return $"{process.ProcessName} (PID {pid}) {path}";
        }
        catch
        {
            return $"PID {pid}";
        }
    }

    private static string ReadTail(string path)
    {
        try
        {
            if (!File.Exists(path)) return "无错误日志";
            var text = File.ReadAllText(path, Encoding.UTF8);
            if (text.Length <= 1200) return text.Trim();
            return text[^1200..].Trim();
        }
        catch (Exception ex)
        {
            return $"读取日志失败：{ex.Message}";
        }
    }

    private static void ExtractPayload(string root)
    {
        var markerPath = Path.Combine(root, "payload.version");
        var appServer = Path.Combine(root, "app", "src", "server.js");
        var nodeExe = Path.Combine(root, "node", "node.exe");
        var exeInfo = new FileInfo(Application.ExecutablePath);
        var payloadVersion = $"{exeInfo.Length}:{exeInfo.LastWriteTimeUtc.Ticks}";
        if (File.Exists(markerPath)
            && File.Exists(appServer)
            && File.Exists(nodeExe)
            && string.Equals(File.ReadAllText(markerPath, Encoding.UTF8).Trim(), payloadVersion, StringComparison.Ordinal))
        {
            return;
        }

        using var stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("payload.zip")
            ?? throw new InvalidOperationException("完整包中缺少 payload。");
        var tempZip = Path.Combine(root, "payload.zip");
        using (var output = File.Create(tempZip))
        {
            stream.CopyTo(output);
        }
        ZipFile.ExtractToDirectory(tempZip, root, overwriteFiles: true);
        File.Delete(tempZip);
        File.WriteAllText(markerPath, payloadVersion, Encoding.UTF8);
    }

    private static string GetInstallRoot()
    {
        return Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "MercadoDiscountManagerStandalone");
    }

    private static async Task<bool> IsHealthy()
    {
        try
        {
            using var client = new HttpClient { Timeout = TimeSpan.FromSeconds(2) };
            var text = await client.GetStringAsync(HealthUrl);
            using var doc = JsonDocument.Parse(text);
            return doc.RootElement.TryGetProperty("ok", out var ok) && ok.GetBoolean();
        }
        catch
        {
            return false;
        }
    }

    private static class UiTheme
    {
        public static readonly Color MainBackground = ColorTranslator.FromHtml("#171B19");
        public static readonly Color SecondaryBackground = ColorTranslator.FromHtml("#1C231F");
        public static readonly Color CardBackground = ColorTranslator.FromHtml("#202820");
        public static readonly Color InputBackground = ColorTranslator.FromHtml("#18201C");
        public static readonly Color TableBackground = ColorTranslator.FromHtml("#171D1A");
        public static readonly Color PrimaryGreen = ColorTranslator.FromHtml("#2F6B3F");
        public static readonly Color SelectedGreen = ColorTranslator.FromHtml("#203A2B");
        public static readonly Color GreenBorder = ColorTranslator.FromHtml("#3E7B4B");
        public static readonly Color GoldBorder = ColorTranslator.FromHtml("#4E472F");
        public static readonly Color GoldFocus = ColorTranslator.FromHtml("#8A7432");
        public static readonly Color MainText = ColorTranslator.FromHtml("#E6E2D8");
        public static readonly Color MutedText = ColorTranslator.FromHtml("#AFA89B");
        public static readonly Color WeakText = ColorTranslator.FromHtml("#777266");
        public static readonly Color NormalBorder = ColorTranslator.FromHtml("#303832");
        public static readonly Color CardBorder = ColorTranslator.FromHtml("#384139");
        public static readonly Color ButtonSecondary = ColorTranslator.FromHtml("#232C24");
        public static readonly Color HoverBackground = ColorTranslator.FromHtml("#26352C");
        public static readonly Color ButtonText = ColorTranslator.FromHtml("#F6F3EA");

        public static void ApplyForm(Form form)
        {
            form.BackColor = MainBackground;
            form.ForeColor = MainText;
            form.Font = new Font("Microsoft YaHei UI", 9F, FontStyle.Regular);
        }

        public static void ApplyControlTree(Control parent)
        {
            foreach (Control control in parent.Controls)
            {
                switch (control)
                {
                    case Button button:
                        StyleButton(button, primary: false);
                        break;
                    case ComboBox combo:
                        StyleCombo(combo);
                        break;
                    case NumericUpDown number:
                        StyleNumber(number);
                        break;
                    case TextBox textBox:
                        StyleTextBox(textBox);
                        break;
                    case GroupBox groupBox:
                        groupBox.BackColor = CardBackground;
                        groupBox.ForeColor = MainText;
                        break;
                    case Label label:
                        if (label.ForeColor == SystemColors.ControlText || label.ForeColor == Color.Black)
                        {
                            label.ForeColor = MainText;
                        }
                        if (label.BackColor == SystemColors.Control) label.BackColor = Color.Transparent;
                        break;
                    case TableLayoutPanel table:
                        table.ForeColor = MainText;
                        break;
                    case Panel panel:
                        panel.BackColor = CardBackground;
                        panel.ForeColor = MainText;
                        break;
                }

                ApplyControlTree(control);
            }
        }

        public static void StyleTextBox(TextBox textBox)
        {
            textBox.BackColor = InputBackground;
            textBox.ForeColor = MainText;
            textBox.BorderStyle = BorderStyle.FixedSingle;
        }

        public static void StyleCombo(ComboBox combo)
        {
            combo.BackColor = InputBackground;
            combo.ForeColor = MainText;
            combo.FlatStyle = FlatStyle.Flat;
        }

        public static void StyleNumber(NumericUpDown number)
        {
            number.BackColor = InputBackground;
            number.ForeColor = MainText;
            number.BorderStyle = BorderStyle.FixedSingle;
        }

        public static void StyleButton(Button button, bool primary)
        {
            button.UseVisualStyleBackColor = false;
            button.FlatStyle = FlatStyle.Flat;
            button.BackColor = primary ? PrimaryGreen : ButtonSecondary;
            button.ForeColor = ButtonText;
            button.FlatAppearance.BorderColor = primary ? GreenBorder : GoldBorder;
            button.FlatAppearance.MouseOverBackColor = primary ? ColorTranslator.FromHtml("#356F45") : HoverBackground;
            button.FlatAppearance.MouseDownBackColor = primary ? SelectedGreen : SecondaryBackground;
            button.FlatAppearance.BorderSize = 1;
        }

        public static void StylePrimaryButton(Button button) => StyleButton(button, primary: true);
    }

    private sealed class MainForm : Form
    {
        private const int ReadProbeCap = 20;
        private const int UnbenchmarkedWriteConcurrency = 2;
        private const int WriteProbeCap = 10000;
        private const int LatestVerifiedWriteStable = 350;
        private const int LatestDailyWriteRecommendation = 320;
        private const int LatestDailyWriteRecommendationMin = 300;
        private Process? _startedService;
        private readonly Task<Process?>? _serviceWarmupTask;
        private readonly HttpClient _http = new() { BaseAddress = new Uri(Url), Timeout = Timeout.InfiniteTimeSpan };
        private readonly ComboBox _modeSelect = new();
        private readonly ComboBox _accountSelect = new();
        private readonly ComboBox _siteSelect = new();
        private readonly ComboBox _sellerActivitySelect = new();
        private readonly ComboBox _officialActivitySelect = new();
        private readonly NumericUpDown _sellerDiscount = new();
        private readonly NumericUpDown _officialDiscount = new();
        private readonly Button _submitButton = new();
        private readonly Button _settingsButton = new();
        private readonly Button _loadActivitiesButton = new();
        private readonly Button _decisionButton = new();
        private readonly Button _previewButton = new();
        private readonly Button _refreshTasksButton = new();
        private readonly Label _todayLabel = new();
        private readonly Label _statusLabel = new();
        private readonly DataGridView _taskGrid = new();
        private readonly ContextMenuStrip _taskMenu = new();
        private readonly TextBox _logBox = new();
        private readonly List<AccountInfo> _accounts = new();
        private readonly Dictionary<string, List<string>> _storeAccountIds = new(StringComparer.OrdinalIgnoreCase);
        private readonly Dictionary<string, string> _storeAliases = new(StringComparer.OrdinalIgnoreCase);

        private string _accountId = "";
        private string _selectedStoreKey = "all";
        private string _authDir = AuthDir;
        private string _outputDir = "";
        private decimal _readConcurrency = 2;
        private decimal _previewConcurrency = 2;
        private decimal _writeConcurrency = 2;
        private bool _updatingSelectors;
        private bool _executionJobRunning;
        private string _currentExecutionJobId = "";
        private readonly HashSet<string> _currentExecutionJobIds = new(StringComparer.OrdinalIgnoreCase);
        private string _lastTaskSelectionDetails = "";

        public MainForm(Process? startedService, Task<Process?>? serviceWarmupTask = null)
        {
            _startedService = startedService;
            _serviceWarmupTask = serviceWarmupTask;
            Text = "美客多折扣管家";
            Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath) ?? Icon;
            StartPosition = FormStartPosition.CenterScreen;
            MinimumSize = new Size(1100, 640);
            Size = new Size(1400, 720);
            UiTheme.ApplyForm(this);

            BuildLayout();
        }

        protected override async void OnShown(EventArgs e)
        {
            base.OnShown(e);
            await InitializeWorkbenchAsync();
        }

        protected override void OnFormClosed(FormClosedEventArgs e)
        {
            _http.Dispose();
            if (_startedService is { HasExited: false })
            {
                try
                {
                    _startedService.Kill(entireProcessTree: true);
                    _startedService.WaitForExit(3000);
                }
                catch
                {
                    // Best-effort cleanup only.
                }
            }
            base.OnFormClosed(e);
        }

        private void BuildLayout()
        {
            var root = new TableLayoutPanel
            {
                Dock = DockStyle.Fill,
                ColumnCount = 1,
                RowCount = 4,
                Padding = new Padding(8),
                BackColor = UiTheme.MainBackground,
                ForeColor = UiTheme.MainText
            };
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 50));
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 46));
            root.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 132));
            Controls.Add(root);

            var toolbar = new TableLayoutPanel
            {
                Dock = DockStyle.Fill,
                ColumnCount = 15,
                RowCount = 1,
                Padding = new Padding(0, 5, 0, 5),
                BackColor = UiTheme.SecondaryBackground,
                ForeColor = UiTheme.MainText
            };
            root.Controls.Add(toolbar, 0, 0);

            foreach (var width in new[] { 44, 96, 42, 140, 42, 96, 72, 135, 58, 72, 140, 58, 94, 66 })
            {
                toolbar.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, width));
            }
            toolbar.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
            toolbar.RowStyles.Add(new RowStyle(SizeType.Percent, 100));

            var col = 0;
            ConfigureCombo(_modeSelect, 92);
            _modeSelect.Items.AddRange(new object[] { "自动判断", "批量报活动", "批量更新", "批量取消" });
            _modeSelect.SelectedIndex = 0;
            AddToolbarLabel(toolbar, "模式", col++);
            toolbar.Controls.Add(_modeSelect, col++, 0);

            ConfigureCombo(_accountSelect, 130);
            _accountSelect.SelectedIndexChanged += async (_, _) => await AccountChangedAsync();
            AddToolbarLabel(toolbar, "店铺", col++);
            toolbar.Controls.Add(_accountSelect, col++, 0);

            ConfigureCombo(_siteSelect, 86);
            _siteSelect.SelectedIndexChanged += async (_, _) =>
            {
                if (!_updatingSelectors) await RefreshActivitiesAsync(false);
            };
            AddToolbarLabel(toolbar, "站点", col++);
            toolbar.Controls.Add(_siteSelect, col++, 0);

            ConfigureCombo(_sellerActivitySelect, 125);
            AddToolbarLabel(toolbar, "自建活动", col++);
            toolbar.Controls.Add(_sellerActivitySelect, col++, 0);

            ConfigureNumber(_sellerDiscount, 5);
            toolbar.Controls.Add(_sellerDiscount, col++, 0);

            ConfigureCombo(_officialActivitySelect, 130);
            AddToolbarLabel(toolbar, "官方活动", col++);
            toolbar.Controls.Add(_officialActivitySelect, col++, 0);

            ConfigureNumber(_officialDiscount, 6);
            toolbar.Controls.Add(_officialDiscount, col++, 0);

            ConfigureButton(_submitButton, "提交执行", 86, primary: true);
            _submitButton.Click += async (_, _) =>
            {
                if (_executionJobRunning)
                {
                    await CancelCurrentExecutionJobAsync();
                    return;
                }
                await SubmitExecutionAsync();
            };
            toolbar.Controls.Add(_submitButton, col++, 0);

            ConfigureButton(_settingsButton, "设置", 56, primary: false);
            _settingsButton.Click += async (_, _) => await ShowSettingsAsync();
            toolbar.Controls.Add(_settingsButton, col++, 0);

            ConfigureButton(_refreshTasksButton, "刷新", 50, primary: false);
            _refreshTasksButton.Click += async (_, _) => await RefreshTasksAsync();

            var decisionPanel = new TableLayoutPanel
            {
                Dock = DockStyle.Fill,
                ColumnCount = 2,
                RowCount = 1,
                Padding = new Padding(0, 3, 0, 3),
                BackColor = UiTheme.MainBackground,
                ForeColor = UiTheme.MainText
            };
            decisionPanel.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
            decisionPanel.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 170));
            root.Controls.Add(decisionPanel, 0, 1);

            _todayLabel.Dock = DockStyle.Fill;
            _todayLabel.BorderStyle = BorderStyle.FixedSingle;
            _todayLabel.BackColor = UiTheme.CardBackground;
            _todayLabel.ForeColor = UiTheme.MainText;
            _todayLabel.TextAlign = ContentAlignment.MiddleLeft;
            _todayLabel.Padding = new Padding(8, 0, 0, 0);
            _todayLabel.Font = new Font("Microsoft YaHei", 10F, FontStyle.Bold);
            _todayLabel.Text = "今日判断：等待判断。提交执行前请核对店铺、站点、活动和折扣。";
            decisionPanel.Controls.Add(_todayLabel, 0, 0);

            ConfigureButton(_loadActivitiesButton, "加载活动", 84, primary: false);
            _loadActivitiesButton.Click += async (_, _) => await LoadActivitiesFromApiAsync();

            ConfigureButton(_decisionButton, "判断今日", 84, primary: false);
            _decisionButton.Click += async (_, _) => await DecideTodayAsync();

            ConfigureButton(_previewButton, "预览", 64, primary: false);
            _previewButton.Click += async (_, _) => await PreviewTodayAsync();

            _statusLabel.AutoSize = true;
            _statusLabel.TextAlign = ContentAlignment.MiddleLeft;
            _statusLabel.Dock = DockStyle.Fill;
            _statusLabel.Padding = new Padding(8, 8, 0, 0);
            _statusLabel.Font = new Font("Microsoft YaHei", 10F, FontStyle.Regular);
            _statusLabel.ForeColor = UiTheme.MutedText;
            decisionPanel.Controls.Add(_statusLabel, 1, 0);

            ConfigureGrid();
            root.Controls.Add(_taskGrid, 0, 2);

            _logBox.Dock = DockStyle.Fill;
            _logBox.Multiline = true;
            _logBox.ScrollBars = ScrollBars.Vertical;
            _logBox.ReadOnly = true;
            _logBox.BackColor = UiTheme.TableBackground;
            _logBox.ForeColor = UiTheme.MainText;
            _logBox.BorderStyle = BorderStyle.FixedSingle;
            _logBox.Font = new Font("Microsoft YaHei", 10F, FontStyle.Regular);
            root.Controls.Add(_logBox, 0, 3);
        }

        private async Task InitializeWorkbenchAsync()
        {
            try
            {
                SetBusy(true, "正在连接本地服务...");
                await CheckHealthAsync();
                await LoadSettingsAsync();
                await LoadAccountsAsync(verifyAccounts: false);
                SetBusy(false, "服务已连接");
                _submitButton.Focus();
                _ = LoadStartupDataAsync();
                Log("工作台已打开。");
            }
            catch (Exception ex)
            {
                Log("初始化失败：" + ex.Message);
                MessageBox.Show(ex.Message, "美客多折扣管家", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
            finally
            {
                SetBusy(false, "服务已连接");
                _submitButton.Focus();
            }
        }

        private async Task LoadStartupDataAsync()
        {
            try
            {
                await Task.WhenAll(RefreshTasksAsync(), RefreshActivitiesAsync(false));
                Log("历史记录和活动下拉已加载。");
            }
            catch (Exception ex)
            {
                Log("启动后台加载失败：" + ex.Message);
            }
        }

        private async Task CheckHealthAsync()
        {
            using var doc = await GetJsonAsync("/api/health");
            if (!Bool(doc.RootElement, "ok")) throw new InvalidOperationException("本地服务未返回正常状态。");
        }

        private async Task LoadSettingsAsync()
        {
            using var doc = await GetJsonAsync("/api/settings");
            if (!doc.RootElement.TryGetProperty("settings", out var settings)) return;
            _authDir = StringValue(settings, "authDir", AuthDir);
            _outputDir = StringValue(settings, "outputDir", "");
            _sellerDiscount.Value = DecimalValue(settings, "sellerDefaultDiscount", 5);
            _officialDiscount.Value = DecimalValue(settings, "officialDefaultDiscount", 6);
            _readConcurrency = DecimalValue(settings, "readConcurrency", 2);
            _previewConcurrency = DecimalValue(settings, "previewConcurrency", 2);
            _writeConcurrency = DecimalValue(settings, "writeConcurrency", 2);
            _storeAliases.Clear();
            if (settings.TryGetProperty("storeAliases", out var aliases) && aliases.ValueKind == JsonValueKind.Object)
            {
                foreach (var property in aliases.EnumerateObject())
                {
                    var alias = property.Value.ToString().Trim();
                    if (property.Name.Length > 0 && alias.Length > 0) _storeAliases[property.Name] = alias;
                }
            }
        }

        private async Task LoadAccountsAsync(bool verifyAccounts = true)
        {
            using var doc = await GetJsonAsync("/api/accounts");
            _updatingSelectors = true;
            try
            {
                _accountSelect.Items.Clear();
                _accounts.Clear();
                _storeAccountIds.Clear();
                if (!doc.RootElement.TryGetProperty("accounts", out var accounts) || accounts.ValueKind != JsonValueKind.Array)
                {
                    Log("未读取到账户列表。");
                    return;
                }

                foreach (var account in accounts.EnumerateArray())
                {
                    var id = StringValue(account, "account_id", "");
                    if (id.Length == 0) continue;
                    if (verifyAccounts)
                    {
                        _accounts.Add(await LoadVerifiedAccountInfoAsync(account, id));
                    }
                    else
                    {
                        var display = AccountDisplayName(account, id);
                        _accounts.Add(new AccountInfo(id, display, StringValue(account, "site_id", ""), StoreNameForAccount(id, display)));
                    }
                }

                BuildStoreItems();
                if (_accountSelect.Items.Count > 0)
                {
                    _accountSelect.SelectedIndex = 0;
                    _selectedStoreKey = ((ComboItem)_accountSelect.SelectedItem!).Value;
                    _accountId = ResolveAccountIdForStore(_selectedStoreKey);
                    Log($"已读取店铺列表：{StoreListText()}。授权账号：{AuthorizedAccountsText()}");
                }
            }
            finally
            {
                _updatingSelectors = false;
            }
        }

        private async Task<AccountInfo> LoadVerifiedAccountInfoAsync(JsonElement account, string accountId)
        {
            var display = AccountDisplayName(account, accountId);
            var site = StringValue(account, "site_id", "");
            try
            {
                using var doc = await PostJsonAsync($"/api/accounts/{Uri.EscapeDataString(accountId)}/verify", new { });
                if (doc.Root.TryGetProperty("account", out var verified))
                {
                    display = AccountDisplayName(verified, accountId);
                    site = StringValue(verified, "site_id", site);
                }
            }
            catch (Exception ex)
            {
                Log($"授权账号 {accountId} 昵称验证未完成，已使用本地信息：" + ex.Message);
            }
            return new AccountInfo(accountId, display, site, StoreNameForAccount(accountId, display));
        }

        private string StoreNameForAccount(string accountId, string display)
        {
            if (_storeAliases.TryGetValue(accountId, out var alias) && alias.Trim().Length > 0)
            {
                return alias.Trim();
            }

            var knownStore = KnownStoreNameForAccountId(accountId);
            return knownStore.Length > 0 ? knownStore : InferStoreName(display);
        }

        private void BuildStoreItems()
        {
            _accountSelect.Items.Clear();
            if (_accounts.Count == 0) return;
            _storeAccountIds["all"] = _accounts.Select(account => account.AccountId).Distinct().ToList();
            _accountSelect.Items.Add(new ComboItem("all", "全部店铺"));
            foreach (var group in _accounts.GroupBy(account => account.StoreName).OrderBy(group => group.Key))
            {
                var storeKey = group.Key;
                _storeAccountIds[storeKey] = group.Select(account => account.AccountId).Distinct().ToList();
                _accountSelect.Items.Add(new ComboItem(storeKey, storeKey));
            }
        }

        private async Task AccountChangedAsync()
        {
            if (_updatingSelectors) return;
            if (_accountSelect.SelectedItem is not ComboItem item) return;
            _selectedStoreKey = item.Value;
            _accountId = ResolveAccountIdForStore(_selectedStoreKey);
            await RefreshActivitiesAsync(false);
            await RefreshTasksAsync();
        }

        private async Task RefreshActivitiesAsync(bool writeLog)
        {
            var accountIds = SelectedAccountIds();
            if (accountIds.Count == 0) return;
            var currentSite = SelectedValue(_siteSelect);
            _updatingSelectors = true;
            try
            {
                _siteSelect.Items.Clear();
                _siteSelect.Items.Add(new ComboItem("", "全部站点"));
                var siteNotes = new List<string>();
                var addedSiteIds = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                foreach (var accountId in accountIds)
                {
                    using var sitesDoc = await GetJsonAsync($"/api/accounts/{Uri.EscapeDataString(accountId)}/sites");
                    if (!sitesDoc.RootElement.TryGetProperty("sites", out var sites) || sites.ValueKind != JsonValueKind.Array) continue;
                    foreach (var site in sites.EnumerateArray())
                    {
                        var id = StringValue(site, "site_id", "");
                        if (id.Length == 0) continue;
                        var total = IntValue(site, "total", 0);
                        var display = SiteDisplayName(id);
                        if (addedSiteIds.Add(id)) _siteSelect.Items.Add(new ComboItem(id, display));
                        var status = StringValue(site, "last_promotion_status", "");
                        var error = StringValue(site, "last_error", "");
                        var logistics = StringValue(site, "logistic_type", "");
                        var logisticsText = logistics.Length > 0 ? $"[{logistics}]" : "";
                        var storeName = StoreNameForAccountId(accountId);
                        siteNotes.Add(error.Length > 0 ? $"{storeName}-{display}{logisticsText}读取失败：{error}" : $"{storeName}-{display}{logisticsText}{total}个活动");
                    }
                }
                if (writeLog && siteNotes.Count > 0) Log("站点活动：" + string.Join("，", siteNotes));
                SelectComboByValue(_siteSelect, currentSite);
                if (_siteSelect.SelectedIndex < 0) _siteSelect.SelectedIndex = 0;
            }
            finally
            {
                _updatingSelectors = false;
            }

            var query = SelectedValue(_siteSelect).Length > 0 ? "?siteId=" + Uri.EscapeDataString(SelectedValue(_siteSelect)) : "";
            _sellerActivitySelect.Items.Clear();
            _officialActivitySelect.Items.Clear();
            _sellerActivitySelect.Items.Add(new ComboItem("", "全部自建活动"));
            _officialActivitySelect.Items.Add(new ComboItem("", "全部官方活动"));
            var totalPromotions = 0;
            var sellerNames = new Dictionary<string, ActivityChoice>(StringComparer.OrdinalIgnoreCase);
            var officialNames = new Dictionary<string, ActivityChoice>(StringComparer.OrdinalIgnoreCase);
            foreach (var accountId in accountIds)
            {
                using var promosDoc = await GetJsonAsync($"/api/accounts/{Uri.EscapeDataString(accountId)}/promotions{query}");
                if (!promosDoc.RootElement.TryGetProperty("promotions", out var promotions) || promotions.ValueKind != JsonValueKind.Array) continue;
                foreach (var promo in promotions.EnumerateArray())
                {
                    totalPromotions += 1;
                    var type = StringValue(promo, "promotion_type", "");
                    var displayName = ActivityDisplayName(promo);
                    if (displayName.Length == 0) continue;
                    var key = NormalizeActivityNameKey(displayName);
                    if (key.Length == 0) continue;
                    if (string.Equals(type, "SELLER_CAMPAIGN", StringComparison.OrdinalIgnoreCase))
                    {
                        AddActivityChoice(sellerNames, key, displayName);
                    }
                    else
                    {
                        AddActivityChoice(officialNames, key, displayName);
                    }
                }
            }
            foreach (var choice in sellerNames.Values.OrderBy(value => value.DisplayName, StringComparer.OrdinalIgnoreCase))
            {
                _sellerActivitySelect.Items.Add(new ComboItem(choice.Key, choice.DisplayName));
            }
            foreach (var choice in officialNames.Values.OrderBy(value => value.DisplayName, StringComparer.OrdinalIgnoreCase))
            {
                _officialActivitySelect.Items.Add(new ComboItem(choice.Key, choice.DisplayName));
            }
            _sellerActivitySelect.SelectedIndex = 0;
            _officialActivitySelect.SelectedIndex = 0;
            UpdateComboDropDownWidth(_sellerActivitySelect);
            UpdateComboDropDownWidth(_officialActivitySelect);
            if (writeLog) Log($"活动已刷新：当前筛选 {totalPromotions} 个。");
            var duplicateNotes = sellerNames.Values.Where(choice => choice.Count > 1).Select(choice => $"自建活动“{choice.DisplayName}”匹配 {choice.Count} 个活动")
                .Concat(officialNames.Values.Where(choice => choice.Count > 1).Select(choice => $"官方活动“{choice.DisplayName}”匹配 {choice.Count} 个活动"))
                .ToArray();
            if (writeLog && duplicateNotes.Length > 0) Log("同名活动：" + string.Join("，", duplicateNotes));
        }

        private async Task LoadActivitiesFromApiAsync()
        {
            var accountIds = SelectedAccountIds();
            if (accountIds.Count == 0) return;
            await RunUiTaskAsync("加载活动", async () =>
            {
                var totalAll = 0;
                foreach (var accountId in accountIds)
                {
                    using var doc = await PostJsonAsync($"/api/accounts/{Uri.EscapeDataString(accountId)}/promotions/fetch", new { });
                    var total = IntValue(doc.Root, "total", 0);
                    totalAll += total;
                    Log($"加载活动完成：{StoreNameForAccountId(accountId)} {total} 个。");
                    if (doc.Root.TryGetProperty("children", out var children) && children.ValueKind == JsonValueKind.Array)
                    {
                        foreach (var child in children.EnumerateArray())
                        {
                            var site = SiteDisplayName(StringValue(child, "site_id", ""));
                            var logistics = StringValue(child, "logistic_type", "");
                            var count = IntValue(child, "total", 0);
                            var status = StringValue(child, "status", "ok");
                            var error = StringValue(child, "error", "");
                            Log(error.Length > 0
                                ? $"  {site}[{logistics}] 活动读取失败：{error}"
                                : $"  {site}[{logistics}] 活动 {count} 个，状态 {status}。");
                        }
                    }
                }
                Log($"加载活动完成：合计 {totalAll} 个。");
                await RefreshActivitiesAsync(false);
            });
        }

        private async Task DecideTodayAsync()
        {
            if (_accountId.Length == 0) return;
            await RunUiTaskAsync("判断今日", async () =>
            {
                using var doc = await PostJsonAsync("/api/today/decision", new
                {
                    accountId = _accountId,
                    filters = BuildFilters()
                });
                if (doc.Root.TryGetProperty("decision", out var decision))
                {
                    RenderTodayDecision(decision);
                    Log("判断今日完成：" + StringValue(decision, "reason", "-"));
                }
            });
        }

        private async Task PreviewTodayAsync()
        {
            if (_accountId.Length == 0) return;
            await RunUiTaskAsync("预览今日", async () =>
            {
                using var doc = await PostJsonAsync("/api/today/preview", new
                {
                    accountId = _accountId,
                    filters = BuildFilters(),
                    priceMode = "discount",
                    sellerDiscountPercent = _sellerDiscount.Value,
                    officialDiscountPercent = _officialDiscount.Value
                });
                if (doc.Root.TryGetProperty("decision", out var decision)) RenderTodayDecision(decision);
                Log("预览今日完成。");
                await RefreshTasksAsync();
            });
        }

        private async Task SubmitExecutionAsync()
        {
            if (_accountId.Length == 0) return;
            using var confirm = new StyledConfirmDialog(
                "提交执行确认",
                "提交执行会按当前模式、店铺、站点、活动和折扣真实报名、更新或取消 Mercado 活动商品。\r\n\r\n执行后会写入批次记录，并返回成功、失败和跳过统计。请确认当前筛选和价格规则无误。",
                "确定",
                "取消");
            if (confirm.ShowDialog(this) != DialogResult.OK)
            {
                Log("提交执行已取消。");
                return;
            }

            await SubmitExecutionJobWrapperAsync();
        }

        private async Task SubmitExecutionJobWrapperAsync()
        {
            try
            {
                SetExecutionBusy(true);
                var action = SelectedSubmitAction();
                var accountIds = SelectedAccountIds();
                if (accountIds.Count == 0) throw new InvalidOperationException("当前店铺没有可用授权账号。");
                Log($"开始{SelectedSubmitModeText()}：店铺={SelectedComboText(_accountSelect)}，站点={SelectedComboText(_siteSelect)}，自建{_sellerDiscount.Value:0}%，官方{_officialDiscount.Value:0}%。");
                Log("加载店铺站点列表...");
                var shopSites = await CountSelectedStoreSitesAsync(accountIds);
                Log("店铺站点列表完成");
                Log($"店铺站点数：{shopSites.Total}");
                Log($"已加载店铺站点：{shopSites.Total} 个");
                Log($"其中当前有活动：{shopSites.Active} 个，未开放/未读取到活动：{shopSites.Inactive} 个");
                var readPlan = await ResolveReadConcurrencyPlanAsync();
                var writePlan = await ResolveWriteConcurrencyPlanAsync();
                var siteConcurrency = Math.Max(1, Math.Min(readPlan.Concurrency, accountIds.Count));
                var activityConcurrency = Math.Max(1, Math.Min(readPlan.Concurrency, ReadProbeCap));
                var perJobWriteConcurrency = writePlan.Concurrency;
                Log($"并发依据：读取并发建议={readPlan.Source}；写入并发建议={writePlan.Source}。");
                Log($"并发处理店铺站点：{shopSites.Total} 个，站点并发={siteConcurrency}，活动并发={activityConcurrency}，商品写入并发={perJobWriteConcurrency}。");
                var queuedStoreNames = accountIds.Select(StoreNameForAccountId).Where(name => name.Length > 0).Distinct(StringComparer.OrdinalIgnoreCase).ToArray();
                Log($"展开店铺任务：{accountIds.Count} 个（{string.Join("、", queuedStoreNames)}）。");
                foreach (var accountId in accountIds)
                {
                    Log($"{StoreNameForAccountId(accountId)}：已加入执行队列。");
                }
                using var semaphore = new SemaphoreSlim(siteConcurrency);
                var tasks = accountIds.Select(async (accountId, index) =>
                {
                    var storeName = StoreNameForAccountId(accountId);
                    if (index >= siteConcurrency)
                    {
                        Log($"{storeName}：等待并发槽位。");
                    }
                    await semaphore.WaitAsync();
                    try
                    {
                        Log($"{storeName}：开始店铺任务。");
                        await StartAndPollExecutionJobAsync(accountId, action, perJobWriteConcurrency, siteConcurrency, activityConcurrency, readPlan.Concurrency);
                        Log($"{storeName}：店铺任务完成。");
                    }
                    catch (Exception ex)
                    {
                        Log($"{storeName}：店铺任务未完整完成：" + FriendlyExecutionErrorMessage(ex));
                        throw;
                    }
                    finally
                    {
                        semaphore.Release();
                    }
                }).ToArray();
                await Task.WhenAll(tasks);
                await RefreshTasksAsync();
            }
            catch (Exception ex)
            {
                var message = FriendlyExecutionErrorMessage(ex);
                Log("提交执行未完整完成：" + message);
                if (!IsBusinessTimeoutOrRequestError(ex))
                {
                    MessageBox.Show(message, "美客多折扣管家", MessageBoxButtons.OK, MessageBoxIcon.Information);
                }
            }
            finally
            {
                _currentExecutionJobId = "";
                lock (_currentExecutionJobIds)
                {
                    _currentExecutionJobIds.Clear();
                }
                SetExecutionBusy(false);
            }
        }

        private async Task StartAndPollExecutionJobAsync(string accountId, string action, int writeConcurrency, int siteConcurrency, int activityConcurrency, int readConcurrency)
        {
            using var doc = await PostJsonAsync("/api/execution/jobs/start", new
            {
                accountId,
                action,
                mode = "real",
                confirmText = "REAL_SUBMIT",
                filters = BuildFilters(),
                storeName = StoreNameForAccountId(accountId),
                selectedStoreName = SelectedComboText(_accountSelect),
                selectedSiteName = SelectedComboText(_siteSelect),
                priceMode = "discount",
                sellerDiscountPercent = _sellerDiscount.Value,
                officialDiscountPercent = _officialDiscount.Value,
                readConcurrency = Math.Max(1, Math.Min(readConcurrency, ReadProbeCap)),
                siteConcurrency,
                activityConcurrency,
                writeConcurrency
            });
            if (!doc.Root.TryGetProperty("job", out var job))
            {
                RenderExecutionResult(doc.StatusCode, doc.Root);
                return;
            }
            var jobId = StringValue(job, "id", "");
            if (jobId.Length == 0) throw new InvalidOperationException("后台执行任务创建失败。");
            _currentExecutionJobId = jobId;
            lock (_currentExecutionJobIds)
            {
                _currentExecutionJobIds.Add(jobId);
            }
            try
            {
                await PollExecutionJobAsync(jobId);
            }
            finally
            {
                lock (_currentExecutionJobIds)
                {
                    _currentExecutionJobIds.Remove(jobId);
                }
            }
        }

        private async Task<(int Total, int Active, int Inactive)> CountSelectedStoreSitesAsync(IReadOnlyList<string> accountIds)
        {
            var selectedSiteId = SelectedValue(_siteSelect);
            var sitesByKey = new Dictionary<string, bool>(StringComparer.OrdinalIgnoreCase);
            foreach (var accountId in accountIds)
            {
                using var sitesDoc = await GetJsonAsync($"/api/accounts/{Uri.EscapeDataString(accountId)}/sites");
                if (!sitesDoc.RootElement.TryGetProperty("sites", out var sites) || sites.ValueKind != JsonValueKind.Array) continue;
                foreach (var site in sites.EnumerateArray())
                {
                    var siteId = StringValue(site, "site_id", "");
                    if (selectedSiteId.Length > 0 && !string.Equals(siteId, selectedSiteId, StringComparison.OrdinalIgnoreCase)) continue;
                    if (siteId.Length == 0) continue;
                    var key = $"{accountId}|{siteId}";
                    var hasActivity = IntValue(site, "total", 0) > 0 || IntValue(site, "last_promotion_count", 0) > 0;
                    sitesByKey[key] = sitesByKey.TryGetValue(key, out var current) ? current || hasActivity : hasActivity;
                }
            }
            var total = sitesByKey.Count;
            var active = sitesByKey.Values.Count(value => value);
            return (total, active, Math.Max(0, total - active));
        }

        private async Task<(int Concurrency, string Source)> ResolveReadConcurrencyPlanAsync()
        {
            try
            {
                using var doc = await GetJsonAsync("/api/concurrency-benchmark/results");
                if (doc.RootElement.TryGetProperty("results", out var results)
                    && results.TryGetProperty("read", out var read)
                    && read.ValueKind == JsonValueKind.Object)
                {
                    var suggested = Math.Max(1, Math.Min(IntValue(read, "suggested_read_concurrency", (int)_readConcurrency), ReadProbeCap));
                    var finished = ShortDate(StringValue(read, "finished_at", ""));
                    var source = finished.Length > 0 ? $"来自 {finished} 只读压测，建议 {suggested}" : $"来自上次只读压测，建议 {suggested}";
                    return (suggested, source);
                }
            }
            catch
            {
                // Fall back to the saved local value when benchmark results are unavailable.
            }
            var fallback = Math.Max(1, Math.Min((int)_readConcurrency, ReadProbeCap));
            return (fallback, $"未实测，使用本地保守值 {fallback}");
        }

        private async Task<(int Concurrency, string Source)> ResolveWriteConcurrencyPlanAsync()
        {
            try
            {
                using var doc = await GetJsonAsync("/api/concurrency-benchmark/results");
                if (doc.RootElement.TryGetProperty("results", out var results)
                    && results.TryGetProperty("write", out var write)
                    && write.ValueKind == JsonValueKind.Object)
                {
                    var stable = IntValue(write, "suggested_write_concurrency", 0);
                    if (stable >= LatestVerifiedWriteStable)
                    {
                        stable = Math.Max(1, Math.Min(stable, WriteProbeCap));
                        var suggested = StableToDailyWriteConcurrency(stable);
                        var finished = ShortDate(StringValue(write, "finished_at", ""));
                        var source = finished.Length > 0
                            ? $"来自 {finished} 真实压测，已验证稳定 {stable}，日常建议 {suggested}"
                            : $"来自上次真实压测，已验证稳定 {stable}，日常建议 {suggested}";
                        return (suggested, source);
                    }
                }
                if (doc.RootElement.TryGetProperty("results", out var latestResults)
                    && latestResults.TryGetProperty("write_latest_status", out var latest)
                    && latest.ValueKind == JsonValueKind.Object)
                {
                    var stable = Math.Max(1, Math.Min(IntValue(latest, "verified_stable_concurrency", LatestVerifiedWriteStable), WriteProbeCap));
                    var suggested = Math.Max(1, Math.Min(IntValue(latest, "daily_recommended_max", LatestDailyWriteRecommendation), WriteProbeCap));
                    return (suggested, $"真实测试线程最新回传：已验证稳定 {stable}，日常建议 {LatestDailyWriteRecommendationMin}-{suggested}");
                }
            }
            catch
            {
                // Fall back to conservative write concurrency when benchmark results are unavailable.
            }
            return (UnbenchmarkedWriteConcurrency, $"未实测，使用保守值 {UnbenchmarkedWriteConcurrency}");
        }

        private static int StableToDailyWriteConcurrency(int stable)
        {
            stable = Math.Max(1, Math.Min(stable, WriteProbeCap));
            if (stable <= 2) return stable;
            return Math.Max(1, Math.Min(WriteProbeCap, (int)Math.Floor(stable * 0.8m)));
        }

        private async Task PollExecutionJobAsync(string jobId)
        {
            var logIndex = 0;
            while (true)
            {
                using var doc = await GetJsonAsync($"/api/execution/jobs/{Uri.EscapeDataString(jobId)}");
                if (!doc.RootElement.TryGetProperty("job", out var job)) throw new InvalidOperationException("后台执行任务状态读取失败。");
                var logProperty = job.TryGetProperty("userLogs", out var userLogs) && userLogs.ValueKind == JsonValueKind.Array ? userLogs : default;
                if (logProperty.ValueKind != JsonValueKind.Array && job.TryGetProperty("logs", out var fallbackLogs) && fallbackLogs.ValueKind == JsonValueKind.Array)
                {
                    logProperty = fallbackLogs;
                }
                if (logProperty.ValueKind == JsonValueKind.Array)
                {
                    var index = 0;
                    foreach (var entry in logProperty.EnumerateArray())
                    {
                        if (index >= logIndex)
                        {
                            var message = StringValue(entry, "message", "");
                            if (message.Length > 0) Log(message);
                        }
                        index += 1;
                    }
                    logIndex = index;
                }
                var status = StringValue(job, "status", "");
                if (string.Equals(status, "completed", StringComparison.OrdinalIgnoreCase)
                    || string.Equals(status, "failed", StringComparison.OrdinalIgnoreCase)
                    || string.Equals(status, "cancelled", StringComparison.OrdinalIgnoreCase))
                {
                    if (job.TryGetProperty("result", out var result) && result.ValueKind == JsonValueKind.Object)
                    {
                        RenderExecutionResult(200, result);
                    }
                    else
                    {
                        Log(TerminalJobMessage(status, StringValue(job, "error", "")));
                    }
                    return;
                }
                await Task.Delay(1500);
            }
        }

        private async Task CancelCurrentExecutionJobAsync()
        {
            if (!_executionJobRunning) return;
            List<string> jobIds;
            lock (_currentExecutionJobIds)
            {
                jobIds = _currentExecutionJobIds.ToList();
            }
            if (jobIds.Count == 0 && _currentExecutionJobId.Length > 0)
            {
                jobIds.Add(_currentExecutionJobId);
            }
            if (jobIds.Count == 0) return;
            try
            {
                foreach (var jobId in jobIds.Distinct(StringComparer.OrdinalIgnoreCase))
                {
                    using var doc = await PostJsonAsync($"/api/execution/jobs/{Uri.EscapeDataString(jobId)}/cancel", new { });
                }
                Log($"已请求停止本次执行，涉及 {jobIds.Count} 个店铺任务，当前活动处理完后会停止。");
            }
            catch (Exception ex)
            {
                Log("停止执行失败：" + ex.Message);
            }
        }

        private async Task RefreshTasksAsync()
        {
            using var doc = await GetJsonAsync("/api/tasks?limit=300");
            _taskGrid.Rows.Clear();
            if (!doc.RootElement.TryGetProperty("tasks", out var tasks) || tasks.ValueKind != JsonValueKind.Array)
            {
                ShowEmptyTasksRow("未读取到批次记录。");
                return;
            }
            var rows = new List<TaskGridRow>();
            foreach (var task in tasks.EnumerateArray())
            {
                AddOrMergeTaskGridRow(rows, BuildTaskGridRow(task));
            }
            foreach (var row in rows)
            {
                var index = _taskGrid.Rows.Add(row.TimeText, row.ActionText, row.SellerActivity, row.OfficialActivity, row.ModeText, row.QuantityText, row.Total, row.Success, row.Failed, row.ReasonText);
                _taskGrid.Rows[index].Tag = row;
                _taskGrid.Rows[index].Cells["reason"].ToolTipText = row.ReasonTooltipText;
            }
            if (rows.Count == 0)
            {
                ShowEmptyTasksRow("暂无批次记录。可点“提交执行”，或在“设置”中刷新账号和店铺。");
            }
        }

        private TaskGridRow BuildTaskGridRow(JsonElement task)
        {
            var taskId = IntValue(task, "id", 0);
            var taskIds = IntArray(task, "task_ids").ToList();
            if (taskIds.Count == 0 && taskId > 0) taskIds.Add(taskId);
            var accountId = StringValue(task, "account_id", "");
            var siteId = StringValue(task, "site_id", "");
            var promotionId = StringValue(task, "promotion_id", "");
            var promotionType = StringValue(task, "promotion_type", "");
            var isBatch = string.Equals(promotionId, "__BATCH__", StringComparison.OrdinalIgnoreCase)
                || string.Equals(promotionType, "BATCH", StringComparison.OrdinalIgnoreCase);
            var isSeller = string.Equals(promotionType, "SELLER_CAMPAIGN", StringComparison.OrdinalIgnoreCase)
                || promotionId.StartsWith("C-", StringComparison.OrdinalIgnoreCase);
            var mode = ModeDisplayName(StringValue(task, "mode", ""));
            var actionRaw = StringValue(task, "action", "");
            var total = IntValue(task, "total_count", 0);
            var success = mode == "预览" ? IntValue(task, "planned_count", 0) : IntValue(task, "success_count", 0);
            var failed = mode == "预览" ? IntValue(task, "skipped_count", 0) : IntValue(task, "failed_count", 0);
            var activityTotal = isBatch ? IntValue(task, "promotions_total", 0) : 1;
            var activityName = isBatch ? BatchActivityDisplayName(task) : TaskActivityDisplayName(task);
            var sellerActivity = isBatch ? StringValue(task, "seller_activity_text", "") : isSeller ? activityName : "";
            var officialActivity = isBatch ? StringValue(task, "official_activity_text", "") : isSeller ? "" : activityName;
            if (isBatch && sellerActivity.Length == 0 && officialActivity.Length == 0)
            {
                sellerActivity = activityName;
                officialActivity = activityName;
            }
            var createdText = StringValue(task, "created_at", "");
            DateTime.TryParse(createdText, out var createdAt);
            var storeText = StringValue(task, "store_name", "");
            if (storeText.Length == 0) storeText = StoreNameForAccountId(accountId);
            var siteText = StringValue(task, "site_name", "");
            if (siteText.Length == 0) siteText = isBatch ? "多个站点" : SiteDisplayName(siteId);
            var hasInlineDetails = task.TryGetProperty("details", out var inlineDetails) && inlineDetails.ValueKind == JsonValueKind.Array;
            var detailLines = hasInlineDetails
                ? TaskDetailLines(task, storeText, siteText, activityName, promotionId, promotionType, total, success, failed, IntValue(task, "skipped_count", 0), TaskReason(task)).ToList()
                : new List<string>();
            var failureReasonDetails = TaskFailureReasonDetails(task).ToList();
            return new TaskGridRow(
                taskIds,
                createdAt,
                ShortDate(createdText),
                LegacyActionText(actionRaw),
                storeText,
                siteText,
                sellerActivity,
                officialActivity,
                mode,
                QuantityText(actionRaw),
                total,
                success,
                failed,
                IntValue(task, "skipped_count", 0),
                StringValue(task, "short_failure_reason", TaskReason(task)),
                TaskMergeKey(createdAt, actionRaw, StringValue(task, "mode", ""), isSeller ? "seller" : isBatch ? "batch" : "official", activityName, isBatch),
                isBatch,
                activityTotal,
                StringValue(task, "summary_json", ""),
                detailLines,
                failureReasonDetails);
        }

        private static IEnumerable<string> TaskDetailLines(JsonElement task, string storeText, string siteText, string activityName, string promotionId, string promotionType, int total, int success, int failed, int skipped, string reason)
        {
            if (task.TryGetProperty("details", out var details) && details.ValueKind == JsonValueKind.Array)
            {
                foreach (var detail in details.EnumerateArray())
                {
                    var detailStore = StringValue(detail, "store_name", storeText);
                    var detailSite = StringValue(detail, "site_name", "");
                    if (detailSite.Length == 0) detailSite = SiteDisplayName(StringValue(detail, "site_id", ""));
                    var detailName = StringValue(detail, "promotion_name", "");
                    if (detailName.Length == 0) detailName = TaskActivityDisplayName(detail);
                    yield return BuildTaskDetailLine(
                        IntValue(detail, "id", 0),
                        detailStore,
                        detailSite,
                        detailName,
                        StringValue(detail, "promotion_id", ""),
                        StringValue(detail, "promotion_type", ""),
                        IntValue(detail, "total_count", 0),
                        IntValue(detail, "success_count", 0),
                        IntValue(detail, "failed_count", 0),
                        IntValue(detail, "skipped_count", 0),
                        TaskReason(detail));
                }
                yield break;
            }
            yield return BuildTaskDetailLine(task.TryGetProperty("id", out var id) && id.TryGetInt32(out var parsed) ? parsed : 0, storeText, siteText, activityName, promotionId, promotionType, total, success, failed, skipped, reason);
        }

        private static string BuildTaskDetailLine(int taskId, string storeText, string siteText, string activityName, string promotionId, string promotionType, int total, int success, int failed, int skipped, string reason)
        {
            var activity = string.IsNullOrWhiteSpace(activityName) ? "未命名活动" : activityName;
            var idText = string.IsNullOrWhiteSpace(promotionId) || promotionId == "__BATCH__"
                ? ""
                : $"（{promotionId} / {PromotionTypeDisplayName(promotionType)}）";
            return $"{storeText} / {siteText} / {activity}{idText}：商品 {total}，成功 {success}，失败 {failed}，跳过 {skipped}。{reason}";
        }

        private static void AddOrMergeTaskGridRow(List<TaskGridRow> rows, TaskGridRow next)
        {
            var existing = rows.FirstOrDefault(row => row.MergeKey == next.MergeKey);
            if (existing == null || next.MergeKey.Length == 0)
            {
                rows.Add(next);
                return;
            }
            existing.TaskIds.AddRange(next.TaskIds);
            existing.StoreNames.UnionWith(next.StoreNames);
            existing.SiteNames.UnionWith(next.SiteNames);
            existing.StoreText = ScopeText(existing.StoreNames, "多个店铺");
            existing.SiteText = ScopeText(existing.SiteNames, "多个站点");
            existing.Total += next.Total;
            existing.Success += next.Success;
            existing.Failed += next.Failed;
            existing.Skipped += next.Skipped;
            existing.SellerActivity = MergeActivityText(existing.SellerActivity, next.SellerActivity);
            existing.OfficialActivity = MergeActivityText(existing.OfficialActivity, next.OfficialActivity);
            existing.ReasonText = MergeReasonText(existing.ReasonText, next.ReasonText);
            existing.DetailLines.AddRange(next.DetailLines);
            existing.FailureReasonDetails.AddRange(next.FailureReasonDetails);
            existing.ActivityTotal += next.ActivityTotal;
            existing.IsBatch = existing.IsBatch || next.IsBatch;
            if (existing.IsBatch)
            {
                existing.ReasonText = AppendScopeHint(existing.ReasonText, existing.StoreNames, existing.SiteNames);
                return;
            }
            var activityCount = existing.TaskIds.Distinct().Count();
            if (existing.SellerActivity.Length > 0 && !existing.SellerActivity.StartsWith("多个活动", StringComparison.Ordinal))
            {
                existing.SellerActivity = ActivityCountText(existing.SellerActivity, activityCount);
            }
            if (existing.OfficialActivity.Length > 0 && !existing.OfficialActivity.StartsWith("多个活动", StringComparison.Ordinal))
            {
                existing.OfficialActivity = ActivityCountText(existing.OfficialActivity, activityCount);
            }
        }

        private static string TaskMergeKey(DateTime createdAt, string action, string mode, string category, string activityName, bool isBatch = false)
        {
            if (isBatch && string.Equals(action, "enroll", StringComparison.OrdinalIgnoreCase)) return "";
            if (!isBatch && (activityName.Length == 0 || activityName.StartsWith("多个活动", StringComparison.Ordinal))) return "";
            var dateKey = createdAt == default ? "" : createdAt.ToString("yyyy-MM-dd");
            var activityKey = isBatch ? "__batch__" : activityName.Trim().ToLowerInvariant();
            return $"{dateKey}|{action}|{mode}|{category}|{activityKey}";
        }

        private static string ActivityCountText(string name, int count)
        {
            var clean = Regex.Replace(name, @"（\d+个活动）$", "");
            return count > 1 ? $"{clean}（{count}个活动）" : clean;
        }

        private static string MergeActivityText(string first, string second)
        {
            var values = new[] { first, second }
                .Where(text => !string.IsNullOrWhiteSpace(text))
                .SelectMany(text => text.Split(new[] { '、', ',', '，' }, StringSplitOptions.RemoveEmptyEntries))
                .Select(text => text.Trim())
                .Where(text => text.Length > 0)
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .OrderBy(text => text, StringComparer.OrdinalIgnoreCase)
                .ToArray();
            return values.Length == 0 ? "" : string.Join("、", values);
        }

        private static string ScopeText(IReadOnlyCollection<string> names, string multiLabel)
        {
            var clean = names.Where(name => !string.IsNullOrWhiteSpace(name))
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .OrderBy(name => name)
                .ToArray();
            if (clean.Length == 0) return "";
            if (clean.Length == 1) return clean[0];
            return $"{multiLabel}（{clean.Length}个）";
        }

        private static string MergeReasonText(string first, string second)
        {
            if (string.IsNullOrWhiteSpace(first)) return second;
            if (string.IsNullOrWhiteSpace(second) || first.Contains(second, StringComparison.Ordinal)) return first;
            return first + "；" + second;
        }

        private static string AppendScopeHint(string reason, IReadOnlyCollection<string> stores, IReadOnlyCollection<string> sites)
        {
            var multiStore = stores.Where(name => !string.IsNullOrWhiteSpace(name)).Distinct(StringComparer.OrdinalIgnoreCase).Count() > 1;
            var multiSite = sites.Where(name => !string.IsNullOrWhiteSpace(name)).Distinct(StringComparer.OrdinalIgnoreCase).Count() > 1;
            if (!multiStore && !multiSite) return reason;
            const string hint = "范围：多个店铺/站点，右键查看详情";
            if (reason.Contains(hint, StringComparison.Ordinal)) return reason;
            return string.IsNullOrWhiteSpace(reason) ? hint : reason + "；" + hint;
        }

        private void CopySelectedTaskRows()
        {
            var rows = SelectedTaskGridRows();
            if (rows.Count == 0)
            {
                Log("请先选择要复制的记录。");
                return;
            }
            var lines = new List<string>
            {
                "时间\t动作\t自建活动\t官方活动\t类型\t数量类型\t商品数\t成功\t失败\t失败原因"
            };
            foreach (var row in rows)
            {
                lines.Add(string.Join("\t", row.Cells.Cast<DataGridViewCell>().Select(cell => Convert.ToString(cell.Value)?.Replace("\t", " ") ?? "")));
            }
            if (TryCopyToClipboard(string.Join(Environment.NewLine, lines)))
            {
                Log($"已复制选中记录 {rows.Count} 行。");
            }
            else
            {
                Log("复制失败：剪贴板暂不可用。");
            }
        }

        private async Task ShowSelectedTaskDetailsAsync()
        {
            var rows = SelectedTaskGridRows();
            if (rows.Count == 0)
            {
                Log("请先选择要查看的记录。");
                return;
            }
            await EnsureSelectedTaskDetailsAsync(rows);
            var detail = BuildSelectedTaskDetails(rows);
            using var form = new TextDetailForm("记录详情", detail);
            form.ShowDialog(this);
        }

        private async Task CopySelectedTaskDetailsAsync()
        {
            var rows = SelectedTaskGridRows();
            if (rows.Count == 0)
            {
                Log("请先选择要复制详情的记录。");
                return;
            }
            await EnsureSelectedTaskDetailsAsync(rows);
            if (TryCopyToClipboard(BuildSelectedTaskDetails(rows)))
            {
                Log($"已复制选中记录详情 {rows.Count} 行。");
            }
            else
            {
                Log("复制详情失败：剪贴板暂不可用。");
            }
        }

        private async Task EnsureSelectedTaskDetailsAsync(IEnumerable<DataGridViewRow> rows)
        {
            foreach (var gridRow in rows)
            {
                if (gridRow.Tag is not TaskGridRow taskRow) continue;
                if (taskRow.DetailLines.Count > 0) continue;
                var ids = taskRow.TaskIds.Distinct().Where(id => id > 0).ToArray();
                if (ids.Length == 0) continue;
                using var doc = await GetJsonAsync("/api/tasks/details?taskIds=" + Uri.EscapeDataString(string.Join(",", ids)));
                if (!doc.RootElement.TryGetProperty("details", out var details) || details.ValueKind != JsonValueKind.Array) continue;
                foreach (var detail in details.EnumerateArray())
                {
                    var detailStore = StringValue(detail, "store_name", taskRow.StoreScopeText);
                    var detailSite = StringValue(detail, "site_name", "");
                    if (detailSite.Length == 0) detailSite = SiteDisplayName(StringValue(detail, "site_id", ""));
                    var detailName = StringValue(detail, "promotion_name", "");
                    if (detailName.Length == 0) detailName = TaskActivityDisplayName(detail);
                    taskRow.DetailLines.Add(BuildTaskDetailLine(
                        IntValue(detail, "id", 0),
                        detailStore,
                        detailSite,
                        detailName,
                        StringValue(detail, "promotion_id", ""),
                        StringValue(detail, "promotion_type", ""),
                        IntValue(detail, "total_count", 0),
                        IntValue(detail, "success_count", 0),
                        IntValue(detail, "failed_count", 0),
                        IntValue(detail, "skipped_count", 0),
                        TaskReason(detail)));
                }
                if (taskRow.DetailLines.Count == 0)
                {
                    taskRow.DetailLines.Add("未读取到详情。");
                }
            }
        }

        private void ShowSelectedTaskSummaryInLog()
        {
            if (_taskGrid.SelectedRows.Count != 1) return;
            if (_taskGrid.SelectedRows[0].Tag is not TaskGridRow taskRow) return;
            if (taskRow.FailureReasonDetails.Count == 0 && taskRow.Skipped == 0) return;
            var builder = new StringBuilder();
            builder.AppendLine($"{taskRow.TimeText} {taskRow.ActionText}：完整失败原因");
            foreach (var reason in taskRow.FailureReasonDetails.Distinct())
            {
                builder.AppendLine("- " + reason);
            }
            if (taskRow.Skipped > 0) builder.AppendLine($"- 跳过数量：{taskRow.Skipped}");
            var text = builder.ToString().Trim();
            if (text == _lastTaskSelectionDetails) return;
            _lastTaskSelectionDetails = text;
            Log(text);
        }

        private static string BuildSelectedTaskDetails(IEnumerable<DataGridViewRow> rows)
        {
            var builder = new StringBuilder();
            foreach (var gridRow in rows)
            {
                if (gridRow.Tag is not TaskGridRow taskRow) continue;
                if (builder.Length > 0) builder.AppendLine().AppendLine();
                builder.AppendLine($"{taskRow.TimeText} {taskRow.ActionText} {taskRow.ModeText}：商品 {taskRow.Total}，成功 {taskRow.Success}，失败 {taskRow.Failed}");
                builder.AppendLine(TaskQuantityNote(taskRow));
                builder.AppendLine($"范围：{taskRow.StoreScopeText} / {taskRow.SiteScopeText}");
                if (taskRow.FailureReasonDetails.Count > 0)
                {
                    builder.AppendLine("失败原因汇总：");
                    foreach (var reason in taskRow.FailureReasonDetails)
                    {
                        builder.AppendLine("- " + reason);
                    }
                }
                builder.AppendLine("明细：");
                foreach (var line in taskRow.DetailLines.Distinct())
                {
                    builder.AppendLine("- " + line);
                }
            }
            return builder.ToString().Trim();
        }

        private async Task DeleteSelectedTaskRowsAsync()
        {
            var rows = SelectedTaskGridRows();
            var ids = rows.SelectMany(row => row.Tag is TaskGridRow taskRow ? taskRow.TaskIds : new List<int>())
                .Distinct()
                .Where(id => id > 0)
                .ToArray();
            if (ids.Length == 0)
            {
                Log("请先选择要删除的本地记录。");
                return;
            }
            var running = rows.Any(row => string.Equals(Convert.ToString(row.Cells["reason"].Value), "执行中", StringComparison.OrdinalIgnoreCase)
                || Convert.ToString(row.Cells["reason"].Value)?.Contains("执行中") == true);
            var message = $"将删除 {ids.Length} 条本地记录及其本地结果明细。\r\n\r\n不会删除 Mercado 活动、商品、授权或设置。";
            if (running) message += "\r\n\r\n选中记录可能仍在运行；删除只影响本地显示记录，不会停止后台任务。";
            if (MessageBox.Show(message, "删除本地记录", MessageBoxButtons.OKCancel, MessageBoxIcon.Warning) != DialogResult.OK) return;

            using var doc = await PostJsonAsync("/api/tasks/delete", new { taskIds = ids });
            if (!Bool(doc.Root, "ok"))
            {
                throw new InvalidOperationException(StringValue(doc.Root, "error", $"删除失败：HTTP {doc.StatusCode}"));
            }
            var deleted = IntValue(doc.Root, "deleted", 0);
            Log($"已删除本地记录 {deleted} 条。");
            await RefreshTasksAsync();
        }

        private List<DataGridViewRow> SelectedTaskGridRows()
        {
            return _taskGrid.SelectedRows.Cast<DataGridViewRow>()
                .Where(row => !row.IsNewRow && row.Tag is TaskGridRow)
                .OrderBy(row => row.Index)
                .ToList();
        }

        private async Task ShowSettingsAsync()
        {
            var benchmarkSummary = await LoadConcurrencyBenchmarkSummaryAsync();
            using var dialog = new SettingsDialog(_authDir, _outputDir, _sellerDiscount.Value, _officialDiscount.Value, _readConcurrency, _previewConcurrency, _writeConcurrency, StoreListText(), AuthorizedAccountsText(), _accounts, _storeAliases, benchmarkSummary);
            dialog.AuthorizeRequestedAsync = async () => await StartOAuthAuthorizationFromConfigAsync(dialog);
            dialog.CompleteAuthorizationRequestedAsync = async () => await CompleteOAuthAuthorizationFromCallbackAsync(dialog);
            dialog.SaveStoreAliasRequestedAsync = async () => await SaveStoreAliasFromSettingsAsync(dialog);
            dialog.RefreshAccountsRequestedAsync = async () =>
            {
                await RunUiTaskAsync("刷新账号/店铺", async () =>
                {
                    await LoadAccountsAsync();
                    await RefreshActivitiesAsync(false);
                    await RefreshTasksAsync();
                    dialog.SetAccountSummary(StoreListText(), AuthorizedAccountsText());
                    dialog.SetAuthorizationStatus("账号/店铺已刷新。");
                });
            };
            var result = dialog.ShowDialog(this);
            if (result != DialogResult.OK) return;
            await RunUiTaskAsync("保存设置", async () =>
            {
                using var doc = await PostJsonAsync("/api/settings", new
                {
                    authDir = dialog.AuthDir,
                    outputDir = dialog.OutputDir,
                    sellerDefaultDiscount = dialog.SellerDiscount,
                    officialDefaultDiscount = dialog.OfficialDiscount,
                    readConcurrency = dialog.ReadConcurrency,
                    previewConcurrency = dialog.PreviewConcurrency,
                    writeConcurrency = dialog.WriteConcurrency,
                    storeAliases = _storeAliases,
                    defaultFilters = BuildFilters()
                });
                await LoadSettingsAsync();
                Log("设置已保存。");
            });
        }

        private async Task<string> LoadConcurrencyBenchmarkSummaryAsync()
        {
            try
            {
                using var doc = await GetJsonAsync("/api/concurrency-benchmark/results");
                if (!doc.RootElement.TryGetProperty("results", out var results))
                {
                    return $"并发实测：后台压测工具已启用逐商品落盘；当前设置={_writeConcurrency:0}；真实测试线程最新回传：350 两次稳定，日常建议 {LatestDailyWriteRecommendationMin}-{LatestDailyWriteRecommendation}。";
                }
                var writeText = $"后台压测工具已启用逐商品落盘；当前设置={_writeConcurrency:0}；真实测试线程最新回传：350 两次稳定，日常建议 {LatestDailyWriteRecommendationMin}-{LatestDailyWriteRecommendation}，追求速度可手动设 350。";
                if (results.TryGetProperty("write_latest_status", out var latest) && latest.ValueKind == JsonValueKind.Object)
                {
                    var stable = IntValue(latest, "verified_stable_concurrency", LatestVerifiedWriteStable);
                    var min = IntValue(latest, "daily_recommended_min", LatestDailyWriteRecommendationMin);
                    var max = IntValue(latest, "daily_recommended_max", LatestDailyWriteRecommendation);
                    var note = StringValue(latest, "verified_note", "350 两次稳定");
                    writeText = $"后台压测工具已启用逐商品落盘；当前设置={_writeConcurrency:0}；当前重复验证最高稳定档：{stable}（真实测试线程，10% update，{note}）；建议日常写入并发：保守 {min}-{max}，追求速度可手动设 350。";
                }
                else if (results.TryGetProperty("write", out var write) && write.ValueKind == JsonValueKind.Object)
                {
                    var stable = IntValue(write, "suggested_write_concurrency", 0);
                    if (stable >= LatestVerifiedWriteStable)
                    {
                        var writeSuggested = StableToDailyWriteConcurrency(stable);
                        var writeFinished = StringValue(write, "finished_at", "");
                        writeText = $"后台压测工具已启用逐商品落盘；当前设置={_writeConcurrency:0}；正式结果已验证稳定写入并发 {stable}，日常建议 {writeSuggested}{(writeFinished.Length > 0 ? "，时间 " + ShortDate(writeFinished) : "")}。";
                    }
                }
                if (results.TryGetProperty("read", out var read) && read.ValueKind == JsonValueKind.Object)
                {
                    var suggested = IntValue(read, "suggested_read_concurrency", 2);
                    var finished = StringValue(read, "finished_at", "");
                    return $"并发实测：上次只读建议读取并发 {suggested}{(finished.Length > 0 ? "，时间 " + ShortDate(finished) : "")}；{writeText}。";
                }
                return $"并发实测：读取并发未实测；{writeText}。";
            }
            catch
            {
                return $"并发实测：后台压测工具已启用逐商品落盘；当前设置={_writeConcurrency:0}；真实测试线程最新回传：350 两次稳定，日常建议 {LatestDailyWriteRecommendationMin}-{LatestDailyWriteRecommendation}。";
            }
        }

        private async Task SaveStoreAliasFromSettingsAsync(SettingsDialog dialog)
        {
            var accountId = dialog.SelectedAliasAccountId;
            var alias = dialog.StoreAliasText;
            if (accountId.Length == 0)
            {
                dialog.SetAliasStatus("没有可设置的授权账号。");
                return;
            }
            if (alias.Length == 0)
            {
                dialog.SetAliasStatus("请输入店铺名称，例如：湖南店。");
                return;
            }
            _storeAliases[accountId] = alias;
            await RunUiTaskAsync("保存店铺名称", async () =>
            {
                using var doc = await PostJsonAsync("/api/settings", new
                {
                    authDir = dialog.AuthDir,
                    outputDir = dialog.OutputDir,
                    sellerDefaultDiscount = dialog.SellerDiscount,
                    officialDefaultDiscount = dialog.OfficialDiscount,
                    readConcurrency = dialog.ReadConcurrency,
                    previewConcurrency = dialog.PreviewConcurrency,
                    writeConcurrency = dialog.WriteConcurrency,
                    storeAliases = _storeAliases,
                    defaultFilters = BuildFilters()
                });
                await LoadSettingsAsync();
                await LoadAccountsAsync();
                dialog.ReloadAliasAccounts(_accounts, _storeAliases);
                dialog.SetAccountSummary(StoreListText(), AuthorizedAccountsText());
                dialog.SetAliasStatus("店铺名称已保存。");
            });
        }

        private async Task StartOAuthAuthorizationFromConfigAsync(SettingsDialog? dialog = null)
        {
            await RunUiTaskAsync("新增账号授权", async () =>
            {
                using var doc = await PostJsonAsync("/api/oauth/start/from-config", new { });
                if (!Bool(doc.Root, "ok"))
                {
                    throw new InvalidOperationException(StringValue(doc.Root, "error", $"授权链接生成失败：HTTP {doc.StatusCode}"));
                }
                var url = StringValue(doc.Root, "authorizationUrl", "");
                if (url.Length == 0) throw new InvalidOperationException("后端未返回授权链接。");
                var warning = StringValue(doc.Root, "warning", "");
                if (warning.Length > 0) Log("授权提示：" + warning);
                var message = "授权链接已复制，请粘贴到目标账号已登录的浏览器中打开。授权完成后复制最终回调地址或 code，回到本程序点“粘贴授权结果”。";
                if (TryCopyToClipboard(url))
                {
                    Log(message);
                    dialog?.SetAuthorizationStatus(message);
                }
                else
                {
                    dialog?.SetAuthorizationStatus("剪贴板复制失败，请在弹窗中手动复制授权链接。");
                    using var linkDialog = new AuthorizationLinkDialog(url);
                    var owner = dialog != null ? (IWin32Window)dialog : this;
                    linkDialog.ShowDialog(owner);
                }
            });
        }

        private async Task CompleteOAuthAuthorizationFromCallbackAsync(SettingsDialog? dialog = null)
        {
            string callbackText;
            if (dialog != null)
            {
                callbackText = dialog.CallbackText;
            }
            else
            {
                using var input = new OAuthCallbackDialog();
                if (input.ShowDialog(this) != DialogResult.OK)
                {
                    Log("授权结果未提交。");
                    return;
                }
                callbackText = input.CallbackText;
            }
            if (callbackText.Length == 0)
            {
                dialog?.SetAuthorizationStatus("请先粘贴回调链接或 code。");
                Log("授权结果未提交：缺少回调链接或 code。");
                return;
            }

            await RunUiTaskAsync("完成账号授权", async () =>
            {
                using var doc = await PostJsonAsync("/api/oauth/complete-callback", new { callbackUrl = callbackText });
                if (!Bool(doc.Root, "ok"))
                {
                    throw new InvalidOperationException(StringValue(doc.Root, "error", $"授权完成失败：HTTP {doc.StatusCode}"));
                }
                var accountName = "新账号";
                if (doc.Root.TryGetProperty("account", out var account))
                {
                    accountName = AccountDisplayName(account, StringValue(account, "account_id", ""));
                }
                Log("授权完成：" + accountName);
                await LoadAccountsAsync();
                dialog?.SetAccountSummary(StoreListText(), AuthorizedAccountsText());
                dialog?.SetAuthorizationStatus("账号授权已保存，账号/店铺摘要已刷新。");
                MessageBox.Show("账号授权已保存。", "账号授权", MessageBoxButtons.OK, MessageBoxIcon.Information);
            });
        }

        private static bool TryCopyToClipboard(string text)
        {
            try
            {
                Clipboard.SetText(text);
                return true;
            }
            catch
            {
                return false;
            }
        }

        private async Task RunUiTaskAsync(string label, Func<Task> action)
        {
            try
            {
                SetBusy(true, label + "...");
                await action();
            }
            catch (Exception ex)
            {
                Log(label + "失败：" + ex.Message);
                if (!IsBusinessTimeoutOrRequestError(ex))
                {
                    MessageBox.Show(ex.Message, "美客多折扣管家", MessageBoxButtons.OK, MessageBoxIcon.Error);
                }
            }
            finally
            {
                SetBusy(false, "服务已连接");
            }
        }

        private object BuildFilters()
        {
            var siteId = SelectedValue(_siteSelect);
            var sellerPromotion = SelectedValue(_sellerActivitySelect);
            var officialPromotion = SelectedValue(_officialActivitySelect);
            return new
            {
                siteId,
                siteIds = siteId.Length > 0 ? new[] { siteId } : Array.Empty<string>(),
                promotionTypes = Array.Empty<string>(),
                keywords = Array.Empty<string>(),
                sellerActivityNames = sellerPromotion.Length > 0 ? new[] { sellerPromotion } : Array.Empty<string>(),
                officialActivityNames = officialPromotion.Length > 0 ? new[] { officialPromotion } : Array.Empty<string>(),
                excludeSeller = false,
                excludeOfficial = false
            };
        }

        private string ResolveAccountIdForStore(string storeKey)
        {
            if (_storeAccountIds.TryGetValue(storeKey, out var accountIds) && accountIds.Count > 0) return accountIds[0];
            return _accounts.FirstOrDefault()?.AccountId ?? "";
        }

        private IReadOnlyList<string> ResolveAccountIdsForStore(string storeKey)
        {
            if (_storeAccountIds.TryGetValue(storeKey, out var accountIds) && accountIds.Count > 0)
            {
                return accountIds.Distinct(StringComparer.OrdinalIgnoreCase).ToList();
            }
            return _accounts.Select(account => account.AccountId).Distinct(StringComparer.OrdinalIgnoreCase).ToList();
        }

        private IReadOnlyList<string> SelectedAccountIds()
        {
            return ResolveAccountIdsForStore(_selectedStoreKey.Length > 0 ? _selectedStoreKey : "all");
        }

        private string StoreNameForAccountId(string accountId)
        {
            var account = _accounts.FirstOrDefault(item => string.Equals(item.AccountId, accountId, StringComparison.OrdinalIgnoreCase));
            if (account is not null) return account.StoreName;
            var knownStore = KnownStoreNameForAccountId(accountId);
            return knownStore.Length > 0 ? knownStore : "店铺待命名";
        }

        private string StoreListText()
        {
            var stores = _accounts.Select(account => account.StoreName).Distinct(StringComparer.OrdinalIgnoreCase).OrderBy(name => name).ToArray();
            return stores.Length == 0 ? "无" : string.Join("、", stores);
        }

        private string AuthorizedAccountsText()
        {
            if (_accounts.Count == 0) return "无";
            var sites = _accounts.Select(account => account.SiteId).Where(site => site.Length > 0).Distinct(StringComparer.OrdinalIgnoreCase).OrderBy(site => site).ToArray();
            return sites.Length == 0 ? $"已授权 {_accounts.Count} 个账号" : $"已授权 {_accounts.Count} 个账号，站点：{string.Join("、", sites)}";
        }

        private static string InferStoreName(string displayName)
        {
            var normalized = displayName.ToUpperInvariant();
            if (normalized.Contains("HUBEI") || displayName.Contains("湖北")) return "湖北店";
            if (normalized.Contains("HUNAN") || displayName.Contains("湖南")) return "湖南店";
            if (normalized.Contains("GUANGDONG") || normalized.Contains("GUANGZHOU") || normalized.Contains("GD") || displayName.Contains("广东")) return "广东店";
            return "店铺待命名";
        }

        private static string KnownStoreNameForAccountId(string accountId)
        {
            return accountId switch
            {
                "2651442567" => "湖北店",
                "3332096437" => "湖南店",
                "3408885754" => "广东店",
                _ => ""
            };
        }

        private void RenderTodayDecision(JsonElement decision)
        {
            var action = StringValue(decision, "action", StringValue(decision, "today_action", "-"));
            var reason = StringValue(decision, "reason", "-");
            var selected = IntValue(decision, "selected_promotions", 0);
            var total = IntValue(decision, "promotions_total", 0);
            var completed = Bool(decision, "already_completed");
            _todayLabel.Text = $"今日判断：{(completed ? "今日已完成" : LegacyActionText(action))} | 活动 {selected}/{total} | {reason}";
        }

        private void RenderExecutionResult(int statusCode, JsonElement root)
        {
            if (root.ValueKind != JsonValueKind.Object)
            {
                Log("提交执行已结束：后台没有返回完整汇总，已保存的结果请查看历史记录。");
                return;
            }
            var hasExecution = root.TryGetProperty("execution", out var execution);
            if (!hasExecution && root.TryGetProperty("prepare", out var prepare))
            {
                RenderPrepareStages(prepare);
            }
            if (hasExecution)
            {
                var total = IntValue(execution, "total", 0);
                var success = IntValue(execution, "success", 0);
                var failed = IntValue(execution, "failed", 0);
                var skipped = IntValue(execution, "skipped", 0);
                var blocked = IntValue(execution, "blocked", 0);
                var displayTotal = Math.Max(total, success + failed + skipped);
                var action = StringValue(root, "action", "-");
                var statusText = Bool(root, "ok") ? "提交执行完成" : "提交执行已停止/部分完成";
                Log($"{statusText}：{LegacyActionText(action)}，商品 {displayTotal}，成功 {success}，失败 {failed}，跳过 {skipped}，阻断活动 {blocked}。");
                if (failed > 0 || blocked > 0) Log("请在批次表查看失败原因。");
                if (root.TryGetProperty("today_decision", out var decision)) RenderTodayDecision(decision);
                return;
            }
            if (root.TryGetProperty("confirmation_package", out var package))
            {
                var status = StringValue(package, "status", "-");
                var planned = IntValue(package, "planned", 0);
                var skipped = IntValue(package, "skipped", 0);
                var blocked = IntValue(package, "blocked", 0);
                Log($"后端返回执行确认信息：HTTP {statusCode}，状态 {status}，可执行 {planned}，跳过 {skipped}，阻断 {blocked}。");
                if (package.TryGetProperty("blocking_reasons", out var reasons) && reasons.ValueKind == JsonValueKind.Array)
                {
                    foreach (var reason in reasons.EnumerateArray()) Log("阻断原因：" + reason.GetString());
                }
                if (package.TryGetProperty("expected_impact_summary", out var impact)) Log("预计影响：" + impact.GetString());
                using var form = new ConfirmationPackageForm(package);
                form.ShowDialog(this);
                return;
            }

            var ok = Bool(root, "ok");
            var message = StringValue(root, "message", StringValue(root, "error", $"HTTP {statusCode}"));
            Log(ok ? $"执行结果：{message}" : $"提交执行失败：{message}");
        }

        private static string TerminalJobMessage(string status, string error)
        {
            var cleanError = FriendlyExecutionErrorMessage(error);
            if (status.Equals("cancelled", StringComparison.OrdinalIgnoreCase))
            {
                return "任务已按停止规则结束，已保存已完成结果，请查看历史记录。" + (cleanError.Length > 0 ? $" 原因：{cleanError}" : "");
            }
            if (status.Equals("failed", StringComparison.OrdinalIgnoreCase))
            {
                return "任务未完整完成，已保存已完成结果，请查看历史记录。" + (cleanError.Length > 0 ? $" 原因：{cleanError}" : "");
            }
            return cleanError.Length > 0 ? $"提交执行结束：{cleanError}" : "提交执行已结束，请查看历史记录。";
        }

        private static string FriendlyExecutionErrorMessage(Exception ex)
        {
            return FriendlyExecutionErrorMessage(ex.Message);
        }

        private static string FriendlyExecutionErrorMessage(string message)
        {
            if (string.IsNullOrWhiteSpace(message)) return "";
            if (message.Contains("requires an element of type 'Object'", StringComparison.OrdinalIgnoreCase)
                || message.Contains("target element has type 'Null'", StringComparison.OrdinalIgnoreCase)
                || message.Contains("JsonElement", StringComparison.OrdinalIgnoreCase))
            {
                return "任务已结束，但后台没有返回完整汇总；已完成结果已保存，请查看历史记录。";
            }
            return message
                .Replace("The requested operation requires an element of type 'Object', but the target element has type 'Null'.", "任务已结束，但后台没有返回完整汇总；已完成结果已保存，请查看历史记录。", StringComparison.OrdinalIgnoreCase)
                .Trim();
        }

        private void RenderPrepareStages(JsonElement prepare)
        {
            if (prepare.TryGetProperty("promotions", out var promotions))
            {
                foreach (var line in StringArray(promotions, "stages")) Log("准备活动：" + line);
            }
            if (prepare.TryGetProperty("items", out var items))
            {
                foreach (var line in StringArray(items, "stages")) Log("准备商品：" + line);
            }
        }

        private string SelectedSubmitAction()
        {
            var mode = _modeSelect.SelectedItem?.ToString() ?? "";
            if (mode.Contains("报活动")) return "enroll";
            if (mode.Contains("更新")) return "update";
            if (mode.Contains("取消")) return "cancel";
            return "";
        }

        private string SelectedSubmitModeText()
        {
            return _modeSelect.SelectedItem?.ToString() ?? "自动判断";
        }

        private static string SelectedComboText(ComboBox combo)
        {
            return combo.SelectedItem is ComboItem item ? item.Text : combo.Text;
        }

        private async Task<ApiJson> PostJsonAsync(string path, object body)
        {
            await EnsureServiceReadyForUiAsync();
            var json = JsonSerializer.Serialize(body);
            using var request = new HttpRequestMessage(HttpMethod.Post, path)
            {
                Content = new StringContent(json, Encoding.UTF8, "application/json")
            };
            using var response = await SendWithServiceErrorAsync((token) => _http.SendAsync(request, token), path, RequestTimeoutFor(path, isPost: true));
            var text = await response.Content.ReadAsStringAsync();
            return new ApiJson((int)response.StatusCode, JsonDocument.Parse(string.IsNullOrWhiteSpace(text) ? "{}" : text));
        }

        private async Task<JsonDocument> GetJsonAsync(string path)
        {
            await EnsureServiceReadyForUiAsync();
            using var request = new HttpRequestMessage(HttpMethod.Get, path);
            using var response = await SendWithServiceErrorAsync((token) => _http.SendAsync(request, token), path, RequestTimeoutFor(path, isPost: false));
            var text = await response.Content.ReadAsStringAsync();
            if (!response.IsSuccessStatusCode)
            {
                throw new InvalidOperationException(text.Length > 0 ? text : $"请求失败：HTTP {(int)response.StatusCode}");
            }
            return JsonDocument.Parse(string.IsNullOrWhiteSpace(text) ? "{}" : text);
        }

        private async Task EnsureServiceReadyForUiAsync()
        {
            if (await IsHealthy()) return;
            if (_serviceWarmupTask != null)
            {
                var warmupProcess = await _serviceWarmupTask;
                if (warmupProcess != null) _startedService = warmupProcess;
                if (await IsHealthy()) return;
            }
            var process = await EnsureServiceAsync();
            if (process != null) _startedService = process;
            if (!await IsHealthy()) throw new InvalidOperationException(BuildServiceUnavailableMessage(null));
        }

        private async Task<HttpResponseMessage> SendWithServiceErrorAsync(Func<CancellationToken, Task<HttpResponseMessage>> send, string path, TimeSpan timeout)
        {
            using var cts = new CancellationTokenSource(timeout);
            try
            {
                return await send(cts.Token);
            }
            catch (HttpRequestException ex)
            {
                if (await IsHealthy()) throw new InvalidOperationException(BuildBusinessRequestMessage(path, ex), ex);
                throw new InvalidOperationException(BuildServiceUnavailableMessage(ex), ex);
            }
            catch (OperationCanceledException ex)
            {
                if (await IsHealthy()) throw new InvalidOperationException(BuildBusinessTimeoutMessage(path, timeout), ex);
                throw new InvalidOperationException(BuildServiceUnavailableMessage(ex), ex);
            }
        }

        private static TimeSpan RequestTimeoutFor(string path, bool isPost)
        {
            if (path.Contains("/api/health", StringComparison.OrdinalIgnoreCase)) return TimeSpan.FromSeconds(5);
            if (path.Contains("/api/today/execute", StringComparison.OrdinalIgnoreCase)
                || path.Contains("/api/batch/execute", StringComparison.OrdinalIgnoreCase)
                || path.Contains("/api/execute", StringComparison.OrdinalIgnoreCase)
                || path.Contains("/api/cancel/filtered/precheck", StringComparison.OrdinalIgnoreCase)
                || path.Contains("/api/batch/items/fetch", StringComparison.OrdinalIgnoreCase)
                || path.Contains("/api/accounts/", StringComparison.OrdinalIgnoreCase) && path.Contains("/promotions/fetch", StringComparison.OrdinalIgnoreCase)
                || path.Contains("/api/inventory-fallback/", StringComparison.OrdinalIgnoreCase))
            {
                return TimeSpan.FromMinutes(10);
            }
            if (path.Contains("/api/oauth/complete-callback", StringComparison.OrdinalIgnoreCase)) return TimeSpan.FromMinutes(3);
            return isPost ? TimeSpan.FromSeconds(90) : TimeSpan.FromSeconds(45);
        }

        private static string BuildServiceUnavailableMessage(Exception? error)
        {
            var root = GetInstallRoot();
            var logDir = Path.Combine(root, "data", "logs");
            var detail = error == null ? "" : $"\r\n底层错误：{error.Message}";
            return $"本地服务未连接，已尝试重新启动内置服务但仍不可用。{detail}\r\n端口：{Port}\r\n端口占用：{DescribeCurrentPortOwner()}\r\n日志目录：{logDir}";
        }

        private static string BuildBusinessTimeoutMessage(string path, TimeSpan timeout)
        {
            return $"业务请求超时：{path} 已等待 {FormatTimeout(timeout)}，本地服务仍在线。\r\n可能正在读取活动/商品或等待 Mercado API 返回。请稍后点“刷新”查看批次结果；如果重复出现，请缩小站点/活动范围后再试。";
        }

        private static string BuildBusinessRequestMessage(string path, Exception error)
        {
            return $"业务请求失败：{path}。\r\n本地服务仍在线，错误来自当前操作或外部 API：{error.Message}";
        }

        private static bool IsBusinessTimeoutOrRequestError(Exception ex)
        {
            var message = ex.Message;
            return message.StartsWith("业务请求超时：", StringComparison.Ordinal)
                || message.StartsWith("业务请求失败：", StringComparison.Ordinal);
        }

        private static string FormatTimeout(TimeSpan timeout)
        {
            return timeout.TotalMinutes >= 1 ? $"{timeout.TotalMinutes:0.#} 分钟" : $"{timeout.TotalSeconds:0} 秒";
        }

        private void SetBusy(bool busy, string status)
        {
            _statusLabel.Text = status;
            foreach (Control control in new Control[] { _submitButton, _settingsButton, _loadActivitiesButton, _decisionButton, _previewButton, _refreshTasksButton })
            {
                control.Enabled = !busy;
            }
        }

        private void SetExecutionBusy(bool busy)
        {
            _executionJobRunning = busy;
            if (busy)
            {
                SetBusy(true, "提交执行...");
                _submitButton.Enabled = true;
                _submitButton.Text = "停止";
                return;
            }
            _submitButton.Text = "提交执行";
            SetBusy(false, "服务已连接");
        }

        private void ConfigureGrid()
        {
            _taskGrid.Dock = DockStyle.Fill;
            _taskGrid.AllowUserToAddRows = false;
            _taskGrid.AllowUserToDeleteRows = false;
            _taskGrid.ReadOnly = true;
            _taskGrid.RowHeadersVisible = false;
            _taskGrid.Font = new Font("Microsoft YaHei", 10F, FontStyle.Regular);
            _taskGrid.SelectionMode = DataGridViewSelectionMode.FullRowSelect;
            _taskGrid.MultiSelect = true;
            _taskGrid.ClipboardCopyMode = DataGridViewClipboardCopyMode.EnableWithoutHeaderText;
            _taskGrid.AutoSizeColumnsMode = DataGridViewAutoSizeColumnsMode.None;
            _taskGrid.AutoSizeRowsMode = DataGridViewAutoSizeRowsMode.None;
            _taskGrid.CellBorderStyle = DataGridViewCellBorderStyle.SingleHorizontal;
            _taskGrid.BackgroundColor = UiTheme.TableBackground;
            _taskGrid.BorderStyle = BorderStyle.FixedSingle;
            _taskGrid.GridColor = UiTheme.NormalBorder;
            _taskGrid.EnableHeadersVisualStyles = false;
            _taskGrid.ColumnHeadersHeightSizeMode = DataGridViewColumnHeadersHeightSizeMode.DisableResizing;
            _taskGrid.ColumnHeadersHeight = 38;
            _taskGrid.ColumnHeadersDefaultCellStyle.BackColor = UiTheme.CardBackground;
            _taskGrid.ColumnHeadersDefaultCellStyle.ForeColor = UiTheme.MainText;
            _taskGrid.ColumnHeadersDefaultCellStyle.SelectionBackColor = UiTheme.CardBackground;
            _taskGrid.ColumnHeadersDefaultCellStyle.SelectionForeColor = UiTheme.MainText;
            _taskGrid.ColumnHeadersDefaultCellStyle.Font = new Font("Microsoft YaHei", 10F, FontStyle.Bold);
            _taskGrid.ColumnHeadersDefaultCellStyle.Padding = new Padding(8, 5, 8, 5);
            _taskGrid.DefaultCellStyle.BackColor = UiTheme.TableBackground;
            _taskGrid.DefaultCellStyle.ForeColor = UiTheme.MainText;
            _taskGrid.DefaultCellStyle.SelectionBackColor = UiTheme.PrimaryGreen;
            _taskGrid.DefaultCellStyle.SelectionForeColor = UiTheme.ButtonText;
            _taskGrid.DefaultCellStyle.Font = _taskGrid.Font;
            _taskGrid.DefaultCellStyle.Padding = new Padding(8, 5, 8, 5);
            _taskGrid.DefaultCellStyle.Alignment = DataGridViewContentAlignment.MiddleLeft;
            _taskGrid.AlternatingRowsDefaultCellStyle.BackColor = UiTheme.CardBackground;
            _taskGrid.AlternatingRowsDefaultCellStyle.ForeColor = UiTheme.MainText;
            _taskGrid.RowTemplate.Height = 34;
            AddGridColumn("time", "时间", 132);
            AddGridColumn("action", "动作", 104);
            AddGridColumn("seller", "自建活动", 108);
            AddGridColumn("official", "官方活动", 108);
            AddGridColumn("type", "类型", 76);
            AddGridColumn("qtyType", "数量类型", 128);
            AddGridColumn("total", "商品数", 78);
            AddGridColumn("success", "成功", 78);
            AddGridColumn("failed", "失败", 78);
            var reason = new DataGridViewTextBoxColumn
            {
                Name = "reason",
                HeaderText = "失败原因",
                AutoSizeMode = DataGridViewAutoSizeColumnMode.Fill,
                MinimumWidth = 420
            };
            _taskGrid.Columns.Add(reason);
            ConfigureTaskContextMenu();
            _taskGrid.CellDoubleClick += async (_, e) =>
            {
                if (e.RowIndex >= 0) await ShowSelectedTaskDetailsAsync();
            };
            _taskGrid.SelectionChanged += (_, _) => ShowSelectedTaskSummaryInLog();
            ShowEmptyTasksRow("正在读取批次记录...");
        }

        private void ConfigureTaskContextMenu()
        {
            _taskMenu.Items.Clear();
            _taskMenu.Items.Add("查看详情", null, async (_, _) => await ShowSelectedTaskDetailsAsync());
            _taskMenu.Items.Add("复制详情", null, async (_, _) => await CopySelectedTaskDetailsAsync());
            _taskMenu.Items.Add(new ToolStripSeparator());
            _taskMenu.Items.Add("复制选中行", null, (_, _) => CopySelectedTaskRows());
            _taskMenu.Items.Add("删除选中记录", null, async (_, _) => await DeleteSelectedTaskRowsAsync());
            _taskMenu.Items.Add("刷新记录", null, async (_, _) => await RefreshTasksAsync());
            _taskGrid.ContextMenuStrip = _taskMenu;
            _taskGrid.MouseDown += (_, e) =>
            {
                if (e.Button != MouseButtons.Right) return;
                var hit = _taskGrid.HitTest(e.X, e.Y);
                if (hit.RowIndex >= 0 && !_taskGrid.Rows[hit.RowIndex].Selected)
                {
                    _taskGrid.ClearSelection();
                    _taskGrid.Rows[hit.RowIndex].Selected = true;
                }
            };
            _taskGrid.KeyDown += async (_, e) =>
            {
                if (e.Control && e.KeyCode == Keys.A)
                {
                    _taskGrid.SelectAll();
                    e.Handled = true;
                }
                else if (e.Control && e.KeyCode == Keys.C)
                {
                    CopySelectedTaskRows();
                    e.Handled = true;
                }
                else if (e.KeyCode == Keys.Delete)
                {
                    await DeleteSelectedTaskRowsAsync();
                    e.Handled = true;
                }
            };
        }

        private void AddGridColumn(string name, string header, int width)
        {
            _taskGrid.Columns.Add(new DataGridViewTextBoxColumn
            {
                Name = name,
                HeaderText = header,
                Width = width,
                SortMode = DataGridViewColumnSortMode.NotSortable
            });
        }

        private void ShowEmptyTasksRow(string message)
        {
            _taskGrid.Rows.Clear();
            var row = _taskGrid.Rows.Add("", "等待操作", "", "", "-", "-", "", "", "", message);
            _taskGrid.Rows[row].DefaultCellStyle.ForeColor = UiTheme.WeakText;
        }

        private void Log(string message)
        {
            if (_logBox.TextLength > 0) _logBox.AppendText(Environment.NewLine);
            _logBox.AppendText($"[{DateTime.Now:HH:mm:ss}] {message}");
        }

        private static void ConfigureCombo(ComboBox combo, int width)
        {
            combo.DropDownStyle = ComboBoxStyle.DropDownList;
            combo.Width = width;
            combo.Font = new Font("Microsoft YaHei", 10F, FontStyle.Regular);
            combo.Dock = DockStyle.Fill;
            combo.Margin = new Padding(2, 2, 6, 2);
            UiTheme.StyleCombo(combo);
            combo.DropDownWidth = width;
        }

        private static void UpdateComboDropDownWidth(ComboBox combo)
        {
            var maxTextWidth = combo.Width;
            foreach (var item in combo.Items)
            {
                var width = TextRenderer.MeasureText(item.ToString(), combo.Font).Width + 36;
                if (width > maxTextWidth) maxTextWidth = width;
            }
            var screen = Screen.FromControl(combo);
            var maxWidth = Math.Min(780, Math.Max(360, screen.WorkingArea.Width - 80));
            combo.DropDownWidth = Math.Max(combo.Width, Math.Min(maxTextWidth, maxWidth));
        }

        private static void ConfigureNumber(NumericUpDown number, decimal value)
        {
            number.Minimum = 1;
            number.Maximum = 90;
            number.DecimalPlaces = 0;
            number.Increment = 1M;
            number.Value = value;
            number.Width = 56;
            number.Font = new Font("Microsoft YaHei", 10F, FontStyle.Regular);
            number.Dock = DockStyle.Fill;
            number.Margin = new Padding(2, 2, 6, 2);
            UiTheme.StyleNumber(number);
        }

        private static void ConfigureButton(Button button, string text, int width, bool primary)
        {
            button.Text = text;
            button.Width = width;
            button.Height = 31;
            button.Font = new Font("Microsoft YaHei", 10F, FontStyle.Regular);
            button.Dock = DockStyle.Fill;
            button.Margin = new Padding(2, 2, 6, 2);
            UiTheme.StyleButton(button, primary);
        }

        private static void AddToolbarLabel(TableLayoutPanel panel, string text, int column)
        {
            panel.Controls.Add(new Label
            {
                Text = text,
                Dock = DockStyle.Fill,
                TextAlign = ContentAlignment.MiddleLeft,
                Padding = new Padding(0, 2, 0, 0),
                Margin = new Padding(0, 0, 2, 0),
                Font = new Font("Microsoft YaHei", 10F, FontStyle.Regular),
                ForeColor = UiTheme.MainText,
                BackColor = Color.Transparent
            }, column, 0);
        }

        private static string SelectedValue(ComboBox combo)
        {
            return combo.SelectedItem is ComboItem item ? item.Value : "";
        }

        private static void SelectComboByValue(ComboBox combo, string value)
        {
            for (var i = 0; i < combo.Items.Count; i++)
            {
                if (combo.Items[i] is ComboItem item && item.Value == value)
                {
                    combo.SelectedIndex = i;
                    return;
                }
            }
        }

        private static void ReplaceComboItemText(ComboBox combo, string value, string text)
        {
            for (var i = 0; i < combo.Items.Count; i++)
            {
                if (combo.Items[i] is ComboItem item && item.Value == value)
                {
                    var selected = combo.SelectedIndex == i;
                    combo.Items[i] = new ComboItem(value, text);
                    if (selected) combo.SelectedIndex = i;
                    return;
                }
            }
        }

        private static string AccountDisplayName(JsonElement account, string accountId)
        {
            var name = StringValue(account, "nickname", "");
            if (name.Length == 0) name = StringValue(account, "display_name", "");
            if (name.StartsWith("Standalone ", StringComparison.OrdinalIgnoreCase)) name = "";
            if (name.Length == 0 || string.Equals(name, accountId, StringComparison.OrdinalIgnoreCase)) name = "账号";
            return name == "账号" ? $"账号 {accountId}" : $"{name}（{accountId}）";
        }

        private static string SiteDisplayName(string siteId)
        {
            return siteId.ToUpperInvariant() switch
            {
                "" => "全部站点",
                "MLB" => "巴西站",
                "MLM" => "墨西哥站",
                "MLA" => "阿根廷站",
                "MLC" => "智利站",
                "MCO" => "哥伦比亚站",
                "MPE" => "秘鲁站",
                "MEC" => "厄瓜多尔站",
                "MLU" => "乌拉圭站",
                "CBT" => "跨境店",
                _ => $"站点 {siteId}"
            };
        }

        private static string PromotionTypeDisplayName(string type)
        {
            return type.ToUpperInvariant() switch
            {
                "SELLER_CAMPAIGN" => "自建活动",
                "DEAL" => "官方活动",
                "SMART" => "智能折扣",
                "LIGHTNING" => "限时秒杀",
                "" => "活动",
                _ => type
            };
        }

        private static string ActivityDisplayName(JsonElement promotion)
        {
            var name = StringValue(promotion, "name", "");
            if (name.Length > 0) return NormalizeActivityDisplayName(name);
            var type = StringValue(promotion, "promotion_type", "");
            var promotionId = StringValue(promotion, "promotion_id", "");
            if (promotionId.Length > 0) return $"{PromotionTypeDisplayName(type)} {promotionId}";
            return NormalizeActivityDisplayName(StringValue(promotion, "id", ""));
        }

        private static void AddActivityChoice(Dictionary<string, ActivityChoice> choices, string key, string displayName)
        {
            if (!choices.TryGetValue(key, out var existing))
            {
                choices[key] = new ActivityChoice(key, displayName, 1);
                return;
            }
            existing.Count += 1;
            if (IsBetterActivityDisplay(displayName, existing.DisplayName))
            {
                existing.DisplayName = displayName;
            }
        }

        private static bool IsBetterActivityDisplay(string candidate, string current)
        {
            if (string.IsNullOrWhiteSpace(current)) return true;
            if (string.IsNullOrWhiteSpace(candidate)) return false;
            return candidate.Length < current.Length;
        }

        private static string NormalizeActivityDisplayName(string value)
        {
            return Regex.Replace(RemoveInvisibleCharacters(value.Normalize(NormalizationForm.FormKC)), @"\s+", " ").Trim().TrimEnd(' ', '.', ',', ';', ':', '|', '/', '\\', '，', '。', '；', '：', '、');
        }

        private static string NormalizeActivityNameKey(string value)
        {
            return NormalizeActivityDisplayName(value).ToLowerInvariant();
        }

        private static string RemoveInvisibleCharacters(string value)
        {
            var builder = new StringBuilder(value.Length);
            foreach (var ch in value)
            {
                var category = CharUnicodeInfo.GetUnicodeCategory(ch);
                if (category == UnicodeCategory.Format || category == UnicodeCategory.Control) continue;
                builder.Append(ch);
            }
            return builder.ToString();
        }

        private static string StringValue(JsonElement element, string name, string fallback)
        {
            return element.TryGetProperty(name, out var value) && value.ValueKind != JsonValueKind.Null
                ? value.ToString()
                : fallback;
        }

        private static int IntValue(JsonElement element, string name, int fallback)
        {
            if (!element.TryGetProperty(name, out var value)) return fallback;
            if (value.ValueKind == JsonValueKind.Number && value.TryGetInt32(out var number)) return number;
            return int.TryParse(value.ToString(), out var parsed) ? parsed : fallback;
        }

        private static IEnumerable<int> IntArray(JsonElement element, string name)
        {
            if (!element.TryGetProperty(name, out var value) || value.ValueKind != JsonValueKind.Array) yield break;
            foreach (var item in value.EnumerateArray())
            {
                if (item.ValueKind == JsonValueKind.Number && item.TryGetInt32(out var number))
                {
                    yield return number;
                }
                else if (int.TryParse(item.ToString(), out var parsed))
                {
                    yield return parsed;
                }
            }
        }

        private static decimal DecimalValue(JsonElement element, string name, decimal fallback)
        {
            if (!element.TryGetProperty(name, out var value)) return fallback;
            if (value.ValueKind == JsonValueKind.Number && value.TryGetDecimal(out var number)) return ClampNumber(number);
            return decimal.TryParse(value.ToString(), out var parsed) ? ClampNumber(parsed) : fallback;
        }

        private static decimal ClampNumber(decimal value) => Math.Max(1, Math.Min(90, value));

        private static bool Bool(JsonElement element, string name)
        {
            return element.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.True;
        }

        private static IEnumerable<string> StringArray(JsonElement element, string name)
        {
            if (!element.TryGetProperty(name, out var value) || value.ValueKind != JsonValueKind.Array) yield break;
            foreach (var item in value.EnumerateArray())
            {
                var text = item.ToString();
                if (!string.IsNullOrWhiteSpace(text)) yield return text;
            }
        }

        private static string ShortDate(string value)
        {
            return DateTime.TryParse(value, out var date) ? date.ToString("yyyy/M/d") : value;
        }

        private static string LegacyActionText(string action)
        {
            return action switch
            {
                "enroll" => "批量报活动",
                "update" => "批量更新",
                "cancel" => "批量取消",
                "completed" => "已完成",
                _ => string.IsNullOrWhiteSpace(action) ? "-" : action
            };
        }

        private static string ModeDisplayName(string mode)
        {
            return mode switch
            {
                "dry-run" => "预览",
                "real" => "提交",
                _ => string.IsNullOrWhiteSpace(mode) ? "-" : mode
            };
        }

        private static string QuantityText(string action)
        {
            return action == "enroll" ? "已报名商品数" : "实际处理数";
        }

        private static string TaskQuantityNote(TaskGridRow taskRow)
        {
            var summary = ParseTaskSummary(taskRow);
            var processed = SummaryInt(summary, "processed_total", taskRow.Success + taskRow.Failed + taskRow.Skipped);
            var candidatePool = SummaryInt(summary, "candidate_pool_total", processed);
            var apiSuccess = SummaryInt(summary, "api_success_count", taskRow.Success);
            var liveVerified = SummaryInt(summary, "live_verified_enrolled_count", -1);
            if (taskRow.QuantityText == "已报名商品数")
            {
                var source = liveVerified >= 0
                    ? $"live 回查确认 {liveVerified}，接口成功 {apiSuccess}"
                    : $"按接口成功统计 {apiSuccess}，未做 live 复核";
                return $"数量口径：主表商品数=真实已报名/上架商品数（{source}）；候选池 {candidatePool}，实际处理 {processed}，失败 {taskRow.Failed}，跳过 {taskRow.Skipped}。";
            }
            return $"数量口径：商品数按实际处理结论合计，成功 {taskRow.Success} + 失败 {taskRow.Failed} + 跳过 {taskRow.Skipped} = 实际处理 {taskRow.Total}。";
        }

        private static JsonElement? ParseTaskSummary(TaskGridRow taskRow)
        {
            var summary = taskRow.SummaryJson;
            if (string.IsNullOrWhiteSpace(summary)) return null;
            try
            {
                using var doc = JsonDocument.Parse(summary);
                return doc.RootElement.Clone();
            }
            catch
            {
                return null;
            }
        }

        private static int SummaryInt(JsonElement? summary, string name, int fallback)
        {
            if (summary.HasValue && summary.Value.TryGetProperty(name, out var value) && value.TryGetInt32(out var parsed)) return parsed;
            return fallback;
        }

        private static string TaskReason(JsonElement task)
        {
            var parts = new List<string>();
            var status = StringValue(task, "status", "");
            var reasons = TaskFailureReasons(task).Take(3).ToList();
            if (reasons.Count > 0)
            {
                parts.Add(string.Join("，", reasons));
            }
            var skipped = IntValue(task, "skipped_count", 0);
            if (skipped > 0) parts.Add("已跳过 " + skipped + " 个");
            var blocked = IntValue(task, "blocked_count", 0);
            if (blocked > 0) parts.Add("阻断活动 " + blocked + " 个");
            var failed = IntValue(task, "failed_count", 0);
            if (failed > 0 && reasons.Count == 0) parts.Add("其他失败 " + failed);
            if (parts.Count == 0)
            {
                var total = IntValue(task, "total_count", 0);
                if (total == 0) parts.Add("未读取到可处理商品");
                else if (status.Length > 0 && !string.Equals(status, "completed", StringComparison.OrdinalIgnoreCase)) parts.Add(TaskStatusDisplayName(status));
            }
            return string.Join("；", parts);
        }

        private static IEnumerable<string> TaskFailureReasons(JsonElement task)
        {
            var results = new List<string>();
            var summary = StringValue(task, "summary_json", "");
            if (summary.Length == 0) return results;
            try
            {
                using var doc = JsonDocument.Parse(summary);
                if (!doc.RootElement.TryGetProperty("failure_reasons", out var reasons) || reasons.ValueKind != JsonValueKind.Array) return results;
                foreach (var reason in reasons.EnumerateArray())
                {
                    var text = StringValue(reason, "reason", "");
                    var count = IntValue(reason, "count", 0);
                    if (text.Length == 0) continue;
                    results.Add(count > 0 ? $"{text} {count}" : text);
                }
            }
            catch
            {
                return results;
            }
            return results;
        }

        private static IEnumerable<string> TaskFailureReasonDetails(JsonElement task)
        {
            if (task.TryGetProperty("full_failure_reasons", out var fullReasons) && fullReasons.ValueKind == JsonValueKind.Array)
            {
                foreach (var line in FormatFailureReasonRows(fullReasons))
                {
                    yield return line;
                }
                yield break;
            }
            var summary = StringValue(task, "summary_json", "");
            if (summary.Length == 0) yield break;
            using var doc = JsonDocument.Parse(summary);
            if (!doc.RootElement.TryGetProperty("failure_reasons", out var reasons) || reasons.ValueKind != JsonValueKind.Array) yield break;
            foreach (var line in FormatFailureReasonRows(reasons))
            {
                yield return line;
            }
        }

        private static IEnumerable<string> FormatFailureReasonRows(JsonElement reasons)
        {
            foreach (var reason in reasons.EnumerateArray())
            {
                var text = StringValue(reason, "reason", "");
                var count = IntValue(reason, "count", 0);
                if (text.Length == 0) continue;
                var sent = reason.TryGetProperty("sent_to_api", out var sentValue) && sentValue.ValueKind == JsonValueKind.False
                    ? "未发送接口"
                    : "已发送接口";
                var suggestion = StringValue(reason, "suggestion", "");
                yield return $"{text}：{count}，{sent}" + (suggestion.Length > 0 ? $"，建议：{suggestion}" : "");
            }
        }

        private static string TaskStatusDisplayName(string status)
        {
            return status switch
            {
                "running" => "执行中",
                "completed" => "已完成",
                "partial_or_failed" => "部分完成/有失败",
                "empty_or_failed" => "未执行/无可处理商品",
                "planned" => "已生成计划",
                "cancelled" => "已停止",
                "canceled" => "已停止",
                "failed" => "执行失败",
                _ => string.IsNullOrWhiteSpace(status) ? "-" : status
            };
        }

        private static string TaskActivityDisplayName(JsonElement task)
        {
            var name = StringValue(task, "promotion_name", "");
            if (name.Length > 0) return name;
            var promotionType = StringValue(task, "promotion_type", "");
            var promotionId = StringValue(task, "promotion_id", "");
            var typeName = PromotionTypeDisplayName(promotionType);
            if (promotionId.Length == 0) return typeName;
            return $"{typeName} {promotionId}";
        }

        private static string BatchActivityDisplayName(JsonElement task)
        {
            var count = IntValue(task, "promotions_total", 0);
            return BatchActivityDisplayName(count);
        }

        private static string BatchActivityDisplayName(int count)
        {
            return count > 0 ? $"多个活动（{count}个）" : "多个活动";
        }
    }

    private sealed record ComboItem(string Value, string Text)
    {
        public override string ToString() => Text;
    }

    private sealed class ActivityChoice
    {
        public ActivityChoice(string key, string displayName, int count)
        {
            Key = key;
            DisplayName = displayName;
            Count = count;
        }

        public string Key { get; }
        public string DisplayName { get; set; }
        public int Count { get; set; }
    }

    private sealed class TaskGridRow
    {
        public TaskGridRow(List<int> taskIds, DateTime createdAt, string timeText, string actionText, string storeText, string siteText, string sellerActivity, string officialActivity, string modeText, string quantityText, int total, int success, int failed, int skipped, string reasonText, string mergeKey, bool isBatch, int activityTotal, string summaryJson, IEnumerable<string> detailLines, IEnumerable<string> failureReasonDetails)
        {
            TaskIds = taskIds;
            CreatedAt = createdAt;
            TimeText = timeText;
            ActionText = actionText;
            StoreText = storeText;
            SiteText = siteText;
            StoreNames = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            SiteNames = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            if (!string.IsNullOrWhiteSpace(storeText)) StoreNames.Add(storeText);
            if (!string.IsNullOrWhiteSpace(siteText)) SiteNames.Add(siteText);
            SellerActivity = sellerActivity;
            OfficialActivity = officialActivity;
            ModeText = modeText;
            QuantityText = quantityText;
            Total = total;
            Success = success;
            Failed = failed;
            Skipped = skipped;
            ReasonText = reasonText;
            MergeKey = mergeKey;
            IsBatch = isBatch;
            ActivityTotal = activityTotal;
            SummaryJson = summaryJson;
            DetailLines = detailLines.Where(line => !string.IsNullOrWhiteSpace(line)).ToList();
            FailureReasonDetails = failureReasonDetails.Where(line => !string.IsNullOrWhiteSpace(line)).ToList();
        }

        public List<int> TaskIds { get; }
        public DateTime CreatedAt { get; }
        public string TimeText { get; }
        public string ActionText { get; }
        public string StoreText { get; set; }
        public string SiteText { get; set; }
        public HashSet<string> StoreNames { get; }
        public HashSet<string> SiteNames { get; }
        public string SellerActivity { get; set; }
        public string OfficialActivity { get; set; }
        public string ModeText { get; }
        public string QuantityText { get; }
        public int Total { get; set; }
        public int Success { get; set; }
        public int Failed { get; set; }
        public int Skipped { get; set; }
        public string ReasonText { get; set; }
        public string MergeKey { get; }
        public bool IsBatch { get; set; }
        public int ActivityTotal { get; set; }
        public string SummaryJson { get; set; }
        public List<string> DetailLines { get; }
        public List<string> FailureReasonDetails { get; }
        public string StoreScopeText => ScopeText(StoreNames, "多个店铺");
        public string SiteScopeText => ScopeText(SiteNames, "多个站点");
        public string ReasonTooltipText => FailureReasonDetails.Count > 0 ? string.Join(Environment.NewLine, FailureReasonDetails) : ReasonText;

        private static string ScopeText(IReadOnlyCollection<string> names, string multiLabel)
        {
            var clean = names.Where(name => !string.IsNullOrWhiteSpace(name))
                .Distinct(StringComparer.OrdinalIgnoreCase)
                .OrderBy(name => name)
                .ToArray();
            if (clean.Length == 0) return "";
            if (clean.Length == 1) return clean[0];
            return $"{multiLabel}（{clean.Length}个）";
        }
    }

    private sealed record AccountInfo(string AccountId, string DisplayName, string SiteId, string StoreName);

    private sealed class ApiJson : IDisposable
    {
        private readonly JsonDocument _document;

        public ApiJson(int statusCode, JsonDocument document)
        {
            StatusCode = statusCode;
            _document = document;
        }

        public int StatusCode { get; }
        public JsonElement Root => _document.RootElement;
        public void Dispose() => _document.Dispose();
    }

    private sealed class TextDetailForm : Form
    {
        public TextDetailForm(string title, string text)
        {
            Text = title;
            Width = 760;
            Height = 520;
            StartPosition = FormStartPosition.CenterParent;
            Font = new Font("Microsoft YaHei", 10F);
            BackColor = UiTheme.MainBackground;
            ForeColor = UiTheme.MainText;

            var box = new TextBox
            {
                Multiline = true,
                ReadOnly = true,
                ScrollBars = ScrollBars.Both,
                Dock = DockStyle.Fill,
                WordWrap = false,
                Text = text,
                BackColor = UiTheme.TableBackground,
                ForeColor = UiTheme.MainText,
                BorderStyle = BorderStyle.FixedSingle,
                Font = new Font("Microsoft YaHei", 10F)
            };
            Controls.Add(box);

            var bottom = new FlowLayoutPanel
            {
                Dock = DockStyle.Bottom,
                Height = 42,
                FlowDirection = FlowDirection.RightToLeft,
                Padding = new Padding(8),
                BackColor = UiTheme.MainBackground
            };
            var close = new Button { Text = "关闭", Width = 82, Height = 26 };
            UiTheme.StyleButton(close, false);
            close.Click += (_, _) => Close();
            var copy = new Button { Text = "复制", Width = 82, Height = 26 };
            UiTheme.StyleButton(copy, true);
            copy.Click += (_, _) => Clipboard.SetText(text);
            bottom.Controls.Add(close);
            bottom.Controls.Add(copy);
            Controls.Add(bottom);
        }
    }

    private sealed class StyledConfirmDialog : Form
    {
        public StyledConfirmDialog(string titleText, string message, string okText, string cancelText)
        {
            Text = titleText;
            StartPosition = FormStartPosition.CenterParent;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            MinimizeBox = false;
            ClientSize = new Size(480, 190);
            UiTheme.ApplyForm(this);

            var title = new Label
            {
                Text = titleText,
                Left = 16,
                Top = 14,
                Width = 448,
                Height = 24,
                Font = new Font("Microsoft YaHei UI", 10F, FontStyle.Bold),
                ForeColor = UiTheme.MainText,
                BackColor = Color.Transparent
            };
            var body = new Label
            {
                Text = message,
                Left = 16,
                Top = 44,
                Width = 448,
                Height = 78,
                ForeColor = UiTheme.MainText,
                BackColor = Color.Transparent
            };
            var ok = new Button { Text = okText, Left = 284, Width = 80, Top = 142, DialogResult = DialogResult.OK };
            var cancel = new Button { Text = cancelText, Left = 380, Width = 80, Top = 142, DialogResult = DialogResult.Cancel };

            Controls.Add(title);
            Controls.Add(body);
            Controls.Add(ok);
            Controls.Add(cancel);
            UiTheme.ApplyControlTree(this);
            UiTheme.StylePrimaryButton(ok);
            UiTheme.StyleButton(cancel, primary: false);
            AcceptButton = ok;
            CancelButton = cancel;
        }
    }

    private sealed class OAuthCallbackDialog : Form
    {
        private readonly TextBox _input = new();

        public OAuthCallbackDialog()
        {
            Text = "粘贴授权结果";
            StartPosition = FormStartPosition.CenterParent;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            MinimizeBox = false;
            ClientSize = new Size(520, 170);
            UiTheme.ApplyForm(this);

            var label = new Label
            {
                Text = "推荐粘贴浏览器地址栏完整回调链接；如果只复制到 code，程序会尝试使用最近一次授权记录自动匹配。",
                Left = 16,
                Top = 16,
                Width = 488,
                Height = 36
            };
            _input.Left = 16;
            _input.Top = 58;
            _input.Width = 488;
            _input.Height = 46;
            _input.Multiline = true;
            _input.ScrollBars = ScrollBars.Vertical;

            var ok = new Button { Text = "完成授权", Left = 320, Width = 88, Top = 122, DialogResult = DialogResult.OK };
            var cancel = new Button { Text = "取消", Left = 424, Width = 80, Top = 122, DialogResult = DialogResult.Cancel };
            Controls.Add(label);
            Controls.Add(_input);
            Controls.Add(ok);
            Controls.Add(cancel);
            UiTheme.ApplyControlTree(this);
            UiTheme.StylePrimaryButton(ok);
            UiTheme.StyleButton(cancel, primary: false);
            AcceptButton = ok;
            CancelButton = cancel;
        }

        public string CallbackText => _input.Text.Trim();
    }

    private sealed class AuthorizationLinkDialog : Form
    {
        public AuthorizationLinkDialog(string url)
        {
            Text = "复制授权链接";
            StartPosition = FormStartPosition.CenterParent;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            MinimizeBox = false;
            ClientSize = new Size(560, 210);
            UiTheme.ApplyForm(this);

            var label = new Label
            {
                Text = "剪贴板复制失败。请手动复制下面的授权链接，到目标账号已登录的浏览器中打开：",
                Left = 16,
                Top = 16,
                Width = 528,
                Height = 34,
                ForeColor = UiTheme.MainText,
                BackColor = Color.Transparent
            };
            var input = new TextBox
            {
                Text = url,
                Left = 16,
                Top = 58,
                Width = 528,
                Height = 86,
                Multiline = true,
                ScrollBars = ScrollBars.Vertical,
                ReadOnly = true,
                BackColor = UiTheme.InputBackground,
                ForeColor = UiTheme.MainText,
                BorderStyle = BorderStyle.FixedSingle
            };
            var ok = new Button { Text = "关闭", Left = 464, Width = 80, Top = 162, DialogResult = DialogResult.OK };
            Controls.Add(label);
            Controls.Add(input);
            Controls.Add(ok);
            UiTheme.ApplyControlTree(this);
            UiTheme.StylePrimaryButton(ok);
            AcceptButton = ok;
        }
    }

    private sealed class ConfirmationPackageForm : Form
    {
        private readonly TextBox _summary = new();

        public ConfirmationPackageForm(JsonElement package)
        {
            Text = "执行确认信息";
            StartPosition = FormStartPosition.CenterParent;
            MinimizeBox = false;
            MaximizeBox = false;
            ClientSize = new Size(720, 460);
            UiTheme.ApplyForm(this);

            var title = new Label
            {
                Text = "执行确认信息",
                Left = 16,
                Top = 14,
                Width = 680,
                Height = 24,
                Font = new Font("Microsoft YaHei UI", 10F, FontStyle.Bold),
                ForeColor = UiTheme.MainText,
                BackColor = Color.Transparent
            };

            _summary.Left = 16;
            _summary.Top = 46;
            _summary.Width = 688;
            _summary.Height = 350;
            _summary.Multiline = true;
            _summary.ScrollBars = ScrollBars.Vertical;
            _summary.ReadOnly = true;
            UiTheme.StyleTextBox(_summary);
            _summary.Text = BuildSummary(package);

            var copy = new Button { Text = "复制摘要", Left = 520, Width = 88, Top = 414 };
            copy.Click += (_, _) => Clipboard.SetText(_summary.Text);
            var close = new Button { Text = "关闭", Left = 624, Width = 80, Top = 414, DialogResult = DialogResult.OK };

            Controls.Add(title);
            Controls.Add(_summary);
            Controls.Add(copy);
            Controls.Add(close);
            UiTheme.ApplyControlTree(this);
            UiTheme.StyleButton(copy, primary: false);
            UiTheme.StylePrimaryButton(close);
            AcceptButton = close;
            CancelButton = close;
        }

        private static string BuildSummary(JsonElement package)
        {
            var lines = new List<string>
            {
                $"状态：{StringValue(package, "status", "-")}",
                $"动作：{LegacyActionText(StringValue(package, "action", "-"))}",
                $"模式：{StringValue(package, "mode", "-")}",
                $"可执行：{IntValue(package, "planned", 0)}",
                $"跳过：{IntValue(package, "skipped", 0)}",
                $"阻断：{IntValue(package, "blocked", 0)}",
                $"写入并发：{IntValue(package, "writeConcurrency", IntValue(package, "write_concurrency", 1))}",
                ""
            };

            if (package.TryGetProperty("expected_impact_summary", out var impact))
            {
                lines.Add("预计影响：");
                lines.Add(impact.ToString());
                lines.Add("");
            }

            if (package.TryGetProperty("blocking_reasons", out var reasons) && reasons.ValueKind == JsonValueKind.Array)
            {
                lines.Add("阻断原因：");
                foreach (var reason in reasons.EnumerateArray()) lines.Add("- " + reason.GetString());
                lines.Add("");
            }

            if (package.TryGetProperty("risk_prompts", out var risks) && risks.ValueKind == JsonValueKind.Array)
            {
                lines.Add("风险提示：");
                foreach (var risk in risks.EnumerateArray()) lines.Add("- " + risk.GetString());
            }

            return string.Join(Environment.NewLine, lines);
        }

        private static string StringValue(JsonElement element, string name, string fallback)
        {
            return element.TryGetProperty(name, out var value) && value.ValueKind != JsonValueKind.Null
                ? value.ToString()
                : fallback;
        }

        private static int IntValue(JsonElement element, string name, int fallback)
        {
            if (!element.TryGetProperty(name, out var value)) return fallback;
            if (value.ValueKind == JsonValueKind.Number && value.TryGetInt32(out var number)) return number;
            return int.TryParse(value.ToString(), out var parsed) ? parsed : fallback;
        }

        private static string LegacyActionText(string action)
        {
            return action switch
            {
                "enroll" => "批量报活动",
                "update" => "批量更新",
                "cancel" => "批量取消",
                _ => string.IsNullOrWhiteSpace(action) ? "-" : action
            };
        }
    }

    private sealed class SettingsDialog : Form
    {
        private readonly TextBox _authDir = new();
        private readonly TextBox _outputDir = new();
        private readonly NumericUpDown _sellerDiscount = new();
        private readonly NumericUpDown _officialDiscount = new();
        private readonly NumericUpDown _readConcurrency = new();
        private readonly NumericUpDown _previewConcurrency = new();
        private readonly NumericUpDown _writeConcurrency = new();
        private readonly ComboBox _aliasAccountSelect = new();
        private readonly TextBox _aliasName = new();
        private readonly Label _aliasStatus = new();
        private readonly TextBox _authSummary = new();
        private readonly Label _authStatus = new();
        private readonly TextBox _callbackInput = new();
        private Dictionary<string, string> _currentAliases = new(StringComparer.OrdinalIgnoreCase);

        public Func<Task>? AuthorizeRequestedAsync { get; set; }
        public Func<Task>? CompleteAuthorizationRequestedAsync { get; set; }
        public Func<Task>? RefreshAccountsRequestedAsync { get; set; }
        public Func<Task>? SaveStoreAliasRequestedAsync { get; set; }

        public SettingsDialog(string authDir, string outputDir, decimal sellerDiscount, decimal officialDiscount, decimal readConcurrency, decimal previewConcurrency, decimal writeConcurrency, string storeSummary, string accountSummary, IReadOnlyList<AccountInfo> accounts, IReadOnlyDictionary<string, string> storeAliases, string concurrencyBenchmarkSummary)
        {
            Text = "设置";
            StartPosition = FormStartPosition.CenterParent;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            MinimizeBox = false;
            ClientSize = new Size(680, 760);
            UiTheme.ApplyForm(this);

            var y = 18;
            AddRow("授权目录", _authDir, authDir, ref y);
            _outputDir.Text = outputDir;
            AddNumberRow("自建默认折扣 %", _sellerDiscount, sellerDiscount, 1, 90, ref y);
            AddNumberRow("官方默认折扣 %", _officialDiscount, officialDiscount, 1, 90, ref y);
            ConfigureHiddenNumber(_readConcurrency, readConcurrency, 1, 20);
            ConfigureHiddenNumber(_previewConcurrency, previewConcurrency, 1, 20);
            ConfigureHiddenNumber(_writeConcurrency, writeConcurrency, 1, 700);

            var note = new Label
            {
                Text = "自动并发策略（推荐）：报名、更新、取消按实测结果和接口反馈调整。日常只需要维护授权、店铺名称和默认折扣。",
                Left = 16,
                Top = y + 4,
                Width = 640,
                Height = 42,
                ForeColor = UiTheme.MutedText,
                BackColor = Color.Transparent
            };
            Controls.Add(note);
            y += 50;

            var benchmarkNote = new Label
            {
                Text = concurrencyBenchmarkSummary,
                Left = 16,
                Top = y,
                Width = 640,
                Height = 44,
                ForeColor = UiTheme.MutedText,
                BackColor = Color.Transparent
            };
            Controls.Add(benchmarkNote);
            y += 50;

            var advancedToggle = new Button { Text = "高级设置", Left = 16, Top = y, Width = 92 };
            Controls.Add(advancedToggle);
            var advancedHint = new Label
            {
                Text = "仅排障、压测或主管要求时调整；普通使用无需修改。",
                Left = 120,
                Top = y + 5,
                Width = 520,
                Height = 24,
                ForeColor = UiTheme.MutedText,
                BackColor = Color.Transparent
            };
            Controls.Add(advancedHint);
            y += 42;

            var aliasGroup = new GroupBox
            {
                Text = "店铺名称",
                Left = 16,
                Top = y,
                Width = 640,
                Height = 105,
                BackColor = UiTheme.CardBackground,
                ForeColor = UiTheme.MainText
            };
            aliasGroup.Controls.Add(new Label { Text = "账号", Left = 14, Top = 28, Width = 60, ForeColor = UiTheme.MainText, BackColor = Color.Transparent });
            _aliasAccountSelect.Left = 78;
            _aliasAccountSelect.Top = 24;
            _aliasAccountSelect.Width = 210;
            _aliasAccountSelect.DropDownStyle = ComboBoxStyle.DropDownList;
            _aliasAccountSelect.SelectedIndexChanged += (_, _) => FillAliasNameFromSelection();
            aliasGroup.Controls.Add(_aliasAccountSelect);
            aliasGroup.Controls.Add(new Label { Text = "店铺名", Left = 306, Top = 28, Width = 60, ForeColor = UiTheme.MainText, BackColor = Color.Transparent });
            _aliasName.Left = 370;
            _aliasName.Top = 24;
            _aliasName.Width = 150;
            aliasGroup.Controls.Add(_aliasName);
            var saveAlias = new Button { Text = "保存店铺名", Left = 532, Top = 23, Width = 92 };
            saveAlias.Click += async (_, _) => await RunDialogActionAsync(SaveStoreAliasRequestedAsync);
            aliasGroup.Controls.Add(saveAlias);
            _aliasStatus.Left = 14;
            _aliasStatus.Top = 64;
            _aliasStatus.Width = 610;
            _aliasStatus.Height = 28;
            _aliasStatus.ForeColor = UiTheme.MutedText;
            _aliasStatus.BackColor = Color.Transparent;
            _aliasStatus.Text = "店铺名只保存在本机，用于主界面显示，不会修改 Mercado 账号。";
            aliasGroup.Controls.Add(_aliasStatus);
            Controls.Add(aliasGroup);
            y += 116;

            var authGroup = new GroupBox
            {
                Text = "账号授权 / 店铺授权",
                Left = 16,
                Top = y,
                Width = 640,
                Height = 245,
                BackColor = UiTheme.CardBackground,
                ForeColor = UiTheme.MainText
            };
            _authSummary.Left = 14;
            _authSummary.Top = 24;
            _authSummary.Width = 610;
            _authSummary.Height = 54;
            _authSummary.Multiline = true;
            _authSummary.ReadOnly = true;
            _authSummary.ScrollBars = ScrollBars.Vertical;
            _authSummary.BackColor = UiTheme.InputBackground;
            _authSummary.ForeColor = UiTheme.MainText;
            _authSummary.BorderStyle = BorderStyle.FixedSingle;
            SetAccountSummary(storeSummary, accountSummary);

            _authStatus.Left = 14;
            _authStatus.Top = 84;
            _authStatus.Width = 610;
            _authStatus.Height = 34;
            _authStatus.ForeColor = UiTheme.MutedText;
            _authStatus.BackColor = Color.Transparent;
            _authStatus.Text = "新增账号时会复制授权链接，不会自动打开浏览器。";

            _callbackInput.Left = 14;
            _callbackInput.Top = 124;
            _callbackInput.Width = 610;
            _callbackInput.Height = 58;
            _callbackInput.Multiline = true;
            _callbackInput.ScrollBars = ScrollBars.Vertical;
            _callbackInput.BackColor = UiTheme.InputBackground;
            _callbackInput.ForeColor = UiTheme.MainText;
            _callbackInput.BorderStyle = BorderStyle.FixedSingle;
            _callbackInput.PlaceholderText = "在这里粘贴最终回调链接或 code";

            var addAccount = new Button { Text = "新增账号授权", Left = 14, Top = 196, Width = 120 };
            addAccount.Click += async (_, _) => await RunDialogActionAsync(AuthorizeRequestedAsync);
            var completeAuth = new Button { Text = "粘贴授权结果", Left = 150, Top = 196, Width = 120 };
            completeAuth.Click += async (_, _) => await RunDialogActionAsync(CompleteAuthorizationRequestedAsync);
            var refreshAccounts = new Button { Text = "刷新账号/店铺", Left = 286, Top = 196, Width = 120 };
            refreshAccounts.Click += async (_, _) => await RunDialogActionAsync(RefreshAccountsRequestedAsync);
            authGroup.Controls.Add(_authSummary);
            authGroup.Controls.Add(_authStatus);
            authGroup.Controls.Add(_callbackInput);
            authGroup.Controls.Add(addAccount);
            authGroup.Controls.Add(completeAuth);
            authGroup.Controls.Add(refreshAccounts);
            Controls.Add(authGroup);
            y += 256;

            var advancedGroup = new GroupBox
            {
                Text = "高级设置 / 诊断",
                Left = 16,
                Top = y,
                Width = 640,
                Height = 150,
                Visible = false,
                BackColor = UiTheme.CardBackground,
                ForeColor = UiTheme.MainText
            };
            var advancedY = 24;
            AddRowTo(advancedGroup, "诊断文件目录", _outputDir, outputDir, ref advancedY);
            AddNumberRowTo(advancedGroup, "读取并发（高级）", _readConcurrency, readConcurrency, 1, 20, ref advancedY);
            AddNumberRowTo(advancedGroup, "活动并发（高级）", _previewConcurrency, previewConcurrency, 1, 20, ref advancedY);
            AddNumberRowTo(advancedGroup, "商品写入并发（当前使用值）", _writeConcurrency, writeConcurrency, 1, 700, ref advancedY);
            Controls.Add(advancedGroup);

            ReloadAliasAccounts(accounts, storeAliases);

            var ok = new Button { Text = "保存", Left = 480, Width = 80, Top = 674, DialogResult = DialogResult.OK };
            var cancel = new Button { Text = "取消", Left = 576, Width = 80, Top = 674, DialogResult = DialogResult.Cancel };
            Controls.Add(ok);
            Controls.Add(cancel);
            UiTheme.ApplyControlTree(this);
            UiTheme.StylePrimaryButton(ok);
            UiTheme.StyleButton(cancel, primary: false);
            UiTheme.StyleButton(addAccount, primary: false);
            UiTheme.StyleButton(completeAuth, primary: false);
            UiTheme.StyleButton(refreshAccounts, primary: false);
            UiTheme.StyleButton(saveAlias, primary: false);
            UiTheme.StyleButton(advancedToggle, primary: false);
            advancedToggle.Click += (_, _) =>
            {
                advancedGroup.Visible = !advancedGroup.Visible;
                advancedToggle.Text = advancedGroup.Visible ? "收起高级" : "高级设置";
                ok.Top = advancedGroup.Visible ? 842 : 674;
                cancel.Top = ok.Top;
                ClientSize = advancedGroup.Visible ? new Size(680, 930) : new Size(680, 760);
            };
            AcceptButton = ok;
            CancelButton = cancel;
        }

        public string CallbackText => _callbackInput.Text.Trim();
        public string SelectedAliasAccountId => _aliasAccountSelect.SelectedItem is ComboItem item ? item.Value : "";
        public string StoreAliasText => _aliasName.Text.Trim();
        public string AuthDir => _authDir.Text.Trim();
        public string OutputDir => _outputDir.Text.Trim();
        public decimal SellerDiscount => _sellerDiscount.Value;
        public decimal OfficialDiscount => _officialDiscount.Value;
        public decimal ReadConcurrency => _readConcurrency.Value;
        public decimal PreviewConcurrency => _previewConcurrency.Value;
        public decimal WriteConcurrency => _writeConcurrency.Value;

        public void SetAuthorizationStatus(string message)
        {
            _authStatus.Text = message;
        }

        public void SetAccountSummary(string storeSummary, string accountSummary)
        {
            _authSummary.Text = $"店铺：{storeSummary}{Environment.NewLine}授权账号：{accountSummary}";
        }

        public void SetAliasStatus(string message)
        {
            _aliasStatus.Text = message;
        }

        public void ReloadAliasAccounts(IReadOnlyList<AccountInfo> accounts, IReadOnlyDictionary<string, string> storeAliases)
        {
            _currentAliases = new Dictionary<string, string>(storeAliases, StringComparer.OrdinalIgnoreCase);
            var selected = SelectedAliasAccountId;
            _aliasAccountSelect.Items.Clear();
            foreach (var account in accounts)
            {
                var site = account.SiteId.Length > 0 ? $" | {account.SiteId}" : "";
                _aliasAccountSelect.Items.Add(new ComboItem(account.AccountId, $"{account.StoreName}{site}"));
            }
            if (_aliasAccountSelect.Items.Count == 0)
            {
                _aliasName.Text = "";
                return;
            }
            var index = 0;
            for (var i = 0; i < _aliasAccountSelect.Items.Count; i++)
            {
                if (_aliasAccountSelect.Items[i] is ComboItem item && item.Value == selected)
                {
                    index = i;
                    break;
                }
            }
            _aliasAccountSelect.SelectedIndex = index;
            FillAliasNameFromSelection(storeAliases);
        }

        private void FillAliasNameFromSelection(IReadOnlyDictionary<string, string>? aliases = null)
        {
            if (_aliasAccountSelect.SelectedItem is not ComboItem item) return;
            var source = aliases ?? _currentAliases;
            if (source.TryGetValue(item.Value, out var alias))
            {
                _aliasName.Text = alias;
                return;
            }
            _aliasName.Text = item.Text.Split('|')[0].Trim();
        }

        private async Task RunDialogActionAsync(Func<Task>? action)
        {
            if (action == null) return;
            try
            {
                UseWaitCursor = true;
                await action();
            }
            catch (Exception ex)
            {
                SetAuthorizationStatus("操作失败：" + ex.Message);
                MessageBox.Show(ex.Message, "美客多折扣管家", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
            finally
            {
                UseWaitCursor = false;
            }
        }

        private void AddRow(string labelText, TextBox textBox, string value, ref int y)
        {
            Controls.Add(new Label { Text = labelText, Left = 16, Top = y + 4, Width = 120, ForeColor = UiTheme.MainText, BackColor = Color.Transparent });
            textBox.Left = 145;
            textBox.Top = y;
            textBox.Width = 500;
            textBox.Text = value;
            UiTheme.StyleTextBox(textBox);
            Controls.Add(textBox);
            y += 34;
        }

        private void AddNumberRow(string labelText, NumericUpDown number, decimal value, decimal min, decimal max, ref int y)
        {
            Controls.Add(new Label { Text = labelText, Left = 16, Top = y + 4, Width = 120, ForeColor = UiTheme.MainText, BackColor = Color.Transparent });
            number.Left = 145;
            number.Top = y;
            number.Width = 90;
            number.Minimum = min;
            number.Maximum = max;
            number.DecimalPlaces = 0;
            number.Increment = 1M;
            number.Value = Math.Max(min, Math.Min(max, value));
            UiTheme.StyleNumber(number);
            Controls.Add(number);
            y += 34;
        }

        private void ConfigureHiddenNumber(NumericUpDown number, decimal value, decimal min, decimal max)
        {
            number.Minimum = min;
            number.Maximum = max;
            number.DecimalPlaces = 0;
            number.Increment = 1M;
            number.Value = Math.Max(min, Math.Min(max, value));
            UiTheme.StyleNumber(number);
        }

        private void AddRowTo(Control parent, string labelText, TextBox textBox, string value, ref int y)
        {
            parent.Controls.Add(new Label { Text = labelText, Left = 14, Top = y + 4, Width = 132, ForeColor = UiTheme.MainText, BackColor = Color.Transparent });
            textBox.Left = 152;
            textBox.Top = y;
            textBox.Width = 460;
            textBox.Text = value;
            UiTheme.StyleTextBox(textBox);
            parent.Controls.Add(textBox);
            y += 30;
        }

        private void AddNumberRowTo(Control parent, string labelText, NumericUpDown number, decimal value, decimal min, decimal max, ref int y)
        {
            parent.Controls.Add(new Label { Text = labelText, Left = 14, Top = y + 4, Width = 170, ForeColor = UiTheme.MainText, BackColor = Color.Transparent });
            number.Left = 190;
            number.Top = y;
            number.Width = 90;
            number.Minimum = min;
            number.Maximum = max;
            number.DecimalPlaces = 0;
            number.Increment = 1M;
            number.Value = Math.Max(min, Math.Min(max, value));
            UiTheme.StyleNumber(number);
            parent.Controls.Add(number);
            y += 30;
        }
    }
}
