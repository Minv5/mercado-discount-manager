using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Globalization;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Net.Http;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace MercadoDiscountManagerStandalone;

internal static class Program
{
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

		public static readonly Color GoldHover = ColorTranslator.FromHtml("#6D5B2A");

		public static readonly Color MainText = ColorTranslator.FromHtml("#E6E2D8");

		public static readonly Color MutedText = ColorTranslator.FromHtml("#AFA89B");

		public static readonly Color WeakText = ColorTranslator.FromHtml("#777266");

		public static readonly Color NormalBorder = ColorTranslator.FromHtml("#303832");

		public static readonly Color CardBorder = ColorTranslator.FromHtml("#384139");

		public static readonly Color ButtonSecondary = ColorTranslator.FromHtml("#232C24");

		public static readonly Color HoverBackground = ColorTranslator.FromHtml("#26352C");

		public static readonly Color NavigationBackground = ColorTranslator.FromHtml("#212923");

		public static readonly Color PrimaryHover = ColorTranslator.FromHtml("#295E38");

		public static readonly Color PrimaryPressed = ColorTranslator.FromHtml("#1F5A34");

		public static readonly Color ButtonText = ColorTranslator.FromHtml("#F6F3EA");

		public static void ApplyForm(Form form)
		{
			form.BackColor = MainBackground;
			form.ForeColor = MainText;
			form.Font = new Font("Microsoft YaHei UI", 9f, FontStyle.Regular);
		}

		public static void ApplyControlTree(Control parent)
		{
			foreach (Control control in parent.Controls)
			{
				if (!(control is Button button))
				{
					if (!(control is ComboBox combo))
					{
						if (!(control is NumericUpDown number))
						{
							if (!(control is TextBox textBox))
							{
								if (!(control is GroupBox groupBox))
								{
									if (!(control is Label label))
									{
										if (!(control is TableLayoutPanel table))
										{
											if (control is Panel panel)
											{
												panel.BackColor = CardBackground;
												panel.ForeColor = MainText;
											}
										}
										else
										{
											table.ForeColor = MainText;
										}
									}
									else
									{
										if (label.ForeColor == SystemColors.ControlText || label.ForeColor == Color.Black)
										{
											label.ForeColor = MainText;
										}
										if (label.BackColor == SystemColors.Control)
										{
											label.BackColor = Color.Transparent;
										}
									}
								}
								else
								{
									groupBox.BackColor = CardBackground;
									groupBox.ForeColor = MainText;
								}
							}
							else
							{
								StyleTextBox(textBox);
							}
						}
						else
						{
							StyleNumber(number);
						}
					}
					else
					{
						StyleCombo(combo);
					}
				}
				else
				{
					StyleButton(button, primary: false);
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
			combo.DrawMode = DrawMode.OwnerDrawFixed;
			combo.ItemHeight = 28;
		}

		public static void StyleNumber(NumericUpDown number)
		{
			number.BackColor = InputBackground;
			number.ForeColor = MainText;
			number.BorderStyle = number is DarkNumericUpDown ? BorderStyle.None : BorderStyle.FixedSingle;
		}

		public static void StyleButton(Button button, bool primary)
		{
			if (button is RoundedButton roundedButton)
			{
				roundedButton.Primary = primary;
				roundedButton.BackColor = primary ? PrimaryGreen : ButtonSecondary;
				roundedButton.ForeColor = primary ? ButtonText : MainText;
				roundedButton.BorderColor = primary ? GoldHover : GoldBorder;
				roundedButton.HoverColor = primary ? PrimaryHover : HoverBackground;
				roundedButton.PressedColor = primary ? PrimaryPressed : SecondaryBackground;
				roundedButton.Invalidate();
				return;
			}
			button.UseVisualStyleBackColor = false;
			button.FlatStyle = FlatStyle.Flat;
			button.BackColor = (primary ? PrimaryGreen : ButtonSecondary);
			button.ForeColor = ButtonText;
			button.FlatAppearance.BorderColor = (primary ? GreenBorder : GoldBorder);
			button.FlatAppearance.MouseOverBackColor = (primary ? ColorTranslator.FromHtml("#356F45") : HoverBackground);
			button.FlatAppearance.MouseDownBackColor = (primary ? SelectedGreen : SecondaryBackground);
			button.FlatAppearance.BorderSize = 1;
		}

		public static void StylePrimaryButton(Button button)
		{
			StyleButton(button, primary: true);
		}
	}

	private sealed class MainForm : Form
	{
		private sealed record ResolvedSubmitDecision(string Action);

		private sealed record StoreDecision(string StoreName, string SiteName, string Action, string Reason);

		private sealed class ExecutionOutcome
		{
			public string StoreName { get; init; } = "";


			public string SiteName { get; init; } = "";


			public string Action { get; init; } = "";


			public int PromotionsTotal { get; init; }

			public int DisplayTotal { get; init; }

			public int Success { get; init; }

			public int Failed { get; init; }

			public int Skipped { get; init; }

			public int SellerProcessed { get; init; }

			public int SellerSuccess { get; init; }

			public int OfficialProcessed { get; init; }

			public int OfficialSuccess { get; init; }

			public int SmartSkipped { get; init; }

			public int LightningSkipped { get; init; }

			public static ExecutionOutcome Empty(string storeName, string siteName, string action)
			{
				return new ExecutionOutcome
				{
					StoreName = storeName,
					SiteName = siteName,
					Action = action
				};
			}

			public static ExecutionOutcome FromResult(string storeName, string siteName, string requestedAction, JsonElement result)
			{
				string action = StringValue(result, "action", requestedAction);
				JsonElement value;
				JsonElement execution = ((result.TryGetProperty("execution", out value) && value.ValueKind == JsonValueKind.Object) ? value : default(JsonElement));
				if (execution.ValueKind != JsonValueKind.Object)
				{
					return Empty(storeName, siteName, action);
				}
				int val = IntValue(execution, "total", 0);
				int success = IntValue(execution, "success", 0);
				int failed = IntValue(execution, "failed", 0);
				int skipped = IntValue(execution, "skipped", 0);
				int displayTotal = Math.Max(val, success + failed + skipped);
				int promotionsTotal = IntValue(execution, "promotions_total", 0);
				int sellerProcessed = 0;
				int sellerSuccess = 0;
				int officialProcessed = 0;
				int officialSuccess = 0;
				int smartSkipped = 0;
				int lightningSkipped = 0;
				if (execution.TryGetProperty("promotions", out var promotions) && promotions.ValueKind == JsonValueKind.Array)
				{
					foreach (JsonElement promotion in promotions.EnumerateArray())
					{
						string type = StringValue(promotion, "promotion_type", "").ToUpperInvariant();
						int itemTotal = Math.Max(IntValue(promotion, "total", 0), IntValue(promotion, "success", 0) + IntValue(promotion, "failed", 0) + IntValue(promotion, "skipped", 0));
						int itemSuccess = IntValue(promotion, "success", 0);
						int itemSkipped = IntValue(promotion, "skipped", 0);
						switch (type)
						{
						case "SELLER_CAMPAIGN":
							sellerProcessed += itemTotal;
							sellerSuccess += itemSuccess;
							break;
						case "DEAL":
							officialProcessed += itemTotal;
							officialSuccess += itemSuccess;
							break;
						case "SMART":
							smartSkipped += itemSkipped;
							break;
						case "LIGHTNING":
							lightningSkipped += itemSkipped;
							break;
						}
					}
				}
				return new ExecutionOutcome
				{
					StoreName = storeName,
					SiteName = siteName,
					Action = action,
					PromotionsTotal = promotionsTotal,
					DisplayTotal = displayTotal,
					Success = success,
					Failed = failed,
					Skipped = skipped,
					SellerProcessed = sellerProcessed,
					SellerSuccess = sellerSuccess,
					OfficialProcessed = officialProcessed,
					OfficialSuccess = officialSuccess,
					SmartSkipped = smartSkipped,
					LightningSkipped = lightningSkipped
				};
			}
		}

		private const int ReadProbeCap = 20;

		private const int UnbenchmarkedWriteConcurrency = 2;

		private const int WriteProbeCap = 10000;

		private const int LatestVerifiedWriteStable = 350;

		private const int LatestDailyWriteRecommendation = 320;

		private const int LatestDailyWriteRecommendationMin = 300;

		private const string WorkbenchPreparingText = "正在准备工作台...";

		private const string WorkbenchReadyText = "工作台已就绪";

		private const string WorkbenchRepairingText = "正在自动修复程序组件...";

		private Process? _startedService;

		private readonly Task<Process?>? _serviceWarmupTask;

		private readonly HttpClient _http = new HttpClient
		{
			BaseAddress = new Uri("http://127.0.0.1:28758"),
			Timeout = Timeout.InfiniteTimeSpan
		};

		private readonly ComboBox _modeSelect = new DarkComboBox();

		private readonly ComboBox _accountSelect = new DarkComboBox();

		private readonly ComboBox _siteSelect = new DarkComboBox();

		private readonly ComboBox _sellerActivitySelect = new DarkComboBox();

		private readonly ComboBox _officialActivitySelect = new DarkComboBox();

		private const string ExcludeActivityValue = "__exclude__";

		private readonly NumericUpDown _sellerDiscount = new DarkNumericUpDown();

		private readonly NumericUpDown _officialDiscount = new DarkNumericUpDown();

		private readonly Button _submitButton = new RoundedButton();

		private readonly Button _settingsButton = new RoundedButton();

		private readonly Button _loadActivitiesButton = new Button();

		private readonly Button _decisionButton = new Button();

		private readonly Button _previewButton = new Button();

		private readonly Button _refreshTasksButton = new RoundedButton();

		private readonly Label _todayLabel = new Label();

		private readonly Label _statusLabel = new Label();

		private readonly DataGridView _taskGrid = new DataGridView();

		private readonly ContextMenuStrip _taskMenu = new ContextMenuStrip();

		private readonly TextBox _logBox = new TextBox();

		private readonly List<AccountInfo> _accounts = new List<AccountInfo>();

		private readonly Dictionary<string, List<string>> _storeAccountIds = new Dictionary<string, List<string>>(StringComparer.OrdinalIgnoreCase);

		private readonly Dictionary<string, string> _storeAliases = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);

		private readonly Dictionary<string, List<string>> _operatingSites = new Dictionary<string, List<string>>(StringComparer.OrdinalIgnoreCase);

		private string _accountId = "";

		private string _selectedStoreKey = "all";

		private string _authDir = "C:\\Users\\dztf6\\Documents\\美客多授权";

		private string _outputDir = "";

		private decimal _readConcurrency = 2m;

		private decimal _previewConcurrency = 2m;

		private decimal _writeConcurrency = 2m;

		private bool _updatingSelectors;

		private bool _executionJobRunning;

		private bool _autoDecisionDataReady;

		private int _autoDecisionRefreshVersion;

		private string _autoResolvedAction = "";

		private decimal _globalTodaySellerDiscount = 5m;

		private decimal _globalTodayOfficialDiscount = 6m;

		private bool _globalTodayDiscountReady;

		private string _globalTodayDiscountMessage = "";

		private string _currentExecutionJobId = "";

		private readonly HashSet<string> _currentExecutionJobIds = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

		private string _lastTaskSelectionDetails = "";

		public MainForm(Process? startedService, Task<Process?>? serviceWarmupTask = null)
		{
			_startedService = startedService;
			_serviceWarmupTask = serviceWarmupTask;
			Text = "美客多折扣管家";
			base.Icon = System.Drawing.Icon.ExtractAssociatedIcon(Application.ExecutablePath) ?? base.Icon;
			base.StartPosition = FormStartPosition.CenterScreen;
			MinimumSize = new Size(1180, 720);
			base.Size = new Size(1440, 900);
			base.FormBorderStyle = FormBorderStyle.Sizable;
			UiTheme.ApplyForm(this);
			BuildLayout();
		}

		protected override void OnHandleCreated(EventArgs e)
		{
			base.OnHandleCreated(e);
			ApplyDarkTitleBar(base.Handle);
		}

		protected override async void OnShown(EventArgs e)
		{
			base.OnShown(e);
			ApplyNativeDarkMode(this);
			ApplyTaskGridColumnWidths();
			await InitializeWorkbenchAsync();
		}

		protected override void OnResizeBegin(EventArgs e)
		{
			base.OnResizeBegin(e);
			_taskGrid.SuspendLayout();
		}

		protected override void OnResizeEnd(EventArgs e)
		{
			_taskGrid.ResumeLayout(performLayout: false);
			ApplyTaskGridColumnWidths();
			PerformLayout();
			InvalidateRoundedControls(this);
			base.OnResizeEnd(e);
		}

		private static void InvalidateRoundedControls(Control root)
		{
			foreach (Control control in root.Controls)
			{
				if (control is RoundedPanel || control is RoundedButton)
				{
					control.Invalidate();
				}
				if (control.HasChildren)
				{
					InvalidateRoundedControls(control);
				}
			}
		}

		protected override void OnFormClosed(FormClosedEventArgs e)
		{
			_http.Dispose();
			Process? startedService = _startedService;
			if (startedService != null && !startedService.HasExited)
			{
				try
				{
					startedService.Kill(entireProcessTree: true);
					startedService.WaitForExit(3000);
				}
				catch
				{
				}
			}
			base.OnFormClosed(e);
		}

		private void BuildLayout()
		{
			base.SuspendLayout();
			TableLayoutPanel root = new TableLayoutPanel
			{
				Dock = DockStyle.Fill,
				ColumnCount = 1,
				RowCount = 2,
				Padding = new Padding(12),
				BackColor = UiTheme.MainBackground,
				ForeColor = UiTheme.MainText
			};
			root.RowStyles.Add(new RowStyle(SizeType.Absolute, 94f));
			root.RowStyles.Add(new RowStyle(SizeType.Percent, 100f));
			base.Controls.Add(root);

			ConfigureCombo(_modeSelect, 92);
			_modeSelect.Items.AddRange(new object[4] { "自动判断", "批量报活动", "批量更新", "批量取消" });
			_modeSelect.SelectedIndex = 0;
			_modeSelect.SelectedIndexChanged += async delegate
			{
				await QueueAutoDecisionRefreshAsync();
			};
			ConfigureCombo(_accountSelect, 130);
			_accountSelect.SelectedIndexChanged += async delegate
			{
				await AccountChangedAsync();
			};
			ConfigureCombo(_siteSelect, 86);
			_siteSelect.SelectedIndexChanged += async delegate
			{
				if (!_updatingSelectors)
				{
					await RefreshActivitiesAsync(writeLog: false);
					await QueueAutoDecisionRefreshAsync();
				}
			};
			ConfigureCombo(_sellerActivitySelect, 125);
			_sellerActivitySelect.SelectedIndexChanged += async delegate
			{
				if (!_updatingSelectors)
				{
					await QueueAutoDecisionRefreshAsync();
				}
			};
			ConfigureNumber(_sellerDiscount, 5m);
			ConfigureCombo(_officialActivitySelect, 130);
			_officialActivitySelect.SelectedIndexChanged += async delegate
			{
				if (!_updatingSelectors)
				{
					await QueueAutoDecisionRefreshAsync();
				}
			};
			ConfigureNumber(_officialDiscount, 6m);
			UpdateDiscountInputState();
			ConfigureButton(_submitButton, "提交执行", 290, primary: true);
			_submitButton.Click += async delegate
			{
				if (_executionJobRunning)
				{
					await CancelCurrentExecutionJobAsync();
				}
				else
				{
					await SubmitExecutionAsync();
				}
			};
			ConfigureNavigationButton(_settingsButton, "设置", 90, selected: false);
			_settingsButton.Click += async delegate
			{
				await ShowSettingsAsync();
			};
			ConfigureButton(_refreshTasksButton, "刷新结果", 290, primary: false);
			_refreshTasksButton.Click += async delegate
			{
				await RefreshTasksAsync();
			};

			ConfigureButton(_loadActivitiesButton, "加载活动", 84, primary: false);
			_loadActivitiesButton.Click += async delegate
			{
				await LoadActivitiesFromApiAsync();
			};
			ConfigureButton(_decisionButton, "判断今日", 84, primary: false);
			_decisionButton.Click += async delegate
			{
				await DecideTodayAsync();
			};
			ConfigureButton(_previewButton, "预览", 64, primary: false);
			_previewButton.Click += async delegate
			{
				await PreviewTodayAsync();
			};

			RoundedPanel header = BuildBrandHeader();
			root.Controls.Add(header, 0, 0);

			TableLayoutPanel workspace = new TableLayoutPanel
			{
				Dock = DockStyle.Fill,
				ColumnCount = 2,
				RowCount = 1,
				BackColor = Color.Transparent,
				Margin = new Padding(0)
			};
			workspace.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 340f));
			workspace.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100f));
			root.Controls.Add(workspace, 0, 1);

			RoundedPanel controlsSurface = BuildControlSurface();
			controlsSurface.Margin = new Padding(0, 0, 6, 0);
			workspace.Controls.Add(controlsSurface, 0, 0);

			TableLayoutPanel resultsArea = new TableLayoutPanel
			{
				Dock = DockStyle.Fill,
				ColumnCount = 1,
				RowCount = 2,
				BackColor = Color.Transparent,
				Margin = new Padding(6, 0, 0, 0)
			};
			resultsArea.RowStyles.Add(new RowStyle(SizeType.Percent, 100f));
			resultsArea.RowStyles.Add(new RowStyle(SizeType.Absolute, 190f));
			workspace.Controls.Add(resultsArea, 1, 0);

			ConfigureGrid();
			_taskGrid.Margin = new Padding(12, 0, 12, 12);
			_taskGrid.BorderStyle = BorderStyle.None;
			RoundedPanel tableSurface = BuildTitledSurface("批次执行结果", _taskGrid);
			tableSurface.Margin = new Padding(0, 0, 0, 6);
			resultsArea.Controls.Add(tableSurface, 0, 0);

			_logBox.Dock = DockStyle.Fill;
			_logBox.Multiline = true;
			_logBox.ScrollBars = ScrollBars.Vertical;
			_logBox.ReadOnly = true;
			_logBox.BackColor = UiTheme.TableBackground;
			_logBox.ForeColor = UiTheme.MainText;
			_logBox.BorderStyle = BorderStyle.None;
			_logBox.Font = new Font("Microsoft YaHei UI", 9.5f, FontStyle.Regular);
			_logBox.Margin = new Padding(12, 0, 12, 12);
			RoundedPanel logSurface = BuildTitledSurface("运行日志", _logBox);
			logSurface.Margin = new Padding(0, 6, 0, 0);
			resultsArea.Controls.Add(logSurface, 0, 1);
			base.ResumeLayout(performLayout: true);
		}

		private RoundedPanel BuildBrandHeader()
		{
			RoundedPanel header = new RoundedPanel
			{
				Dock = DockStyle.Fill,
				Margin = new Padding(0, 0, 0, 10),
				Padding = new Padding(14, 10, 14, 10),
				FillColor = UiTheme.SecondaryBackground,
				BorderColor = UiTheme.GoldBorder
			};
			TableLayoutPanel layout = new TableLayoutPanel
			{
				Dock = DockStyle.Fill,
				ColumnCount = 2,
				RowCount = 1,
				BackColor = Color.Transparent,
				Margin = new Padding(0)
			};
			layout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100f));
			layout.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 430f));

			TableLayoutPanel brand = new TableLayoutPanel
			{
				Dock = DockStyle.Fill,
				ColumnCount = 2,
				RowCount = 1,
				BackColor = Color.Transparent,
				Margin = new Padding(0)
			};
			brand.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 54f));
			brand.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100f));
			PictureBox icon = new PictureBox
			{
				Dock = DockStyle.Fill,
				Margin = new Padding(0, 2, 10, 2),
				SizeMode = PictureBoxSizeMode.Zoom,
				Image = base.Icon?.ToBitmap(),
				BackColor = Color.Transparent
			};
			brand.Controls.Add(icon, 0, 0);
			TableLayoutPanel brandText = new TableLayoutPanel
			{
				Dock = DockStyle.Fill,
				ColumnCount = 1,
				RowCount = 2,
				BackColor = Color.Transparent,
				Margin = new Padding(0)
			};
			brandText.RowStyles.Add(new RowStyle(SizeType.Absolute, 35f));
			brandText.RowStyles.Add(new RowStyle(SizeType.Percent, 100f));
			brandText.Controls.Add(new Label
			{
				Text = "美客多折扣管家",
				Dock = DockStyle.Fill,
				TextAlign = ContentAlignment.BottomLeft,
				Font = new Font("Microsoft YaHei UI", 15f, FontStyle.Bold),
				ForeColor = UiTheme.ButtonText,
				BackColor = Color.Transparent,
				Margin = new Padding(0)
			}, 0, 0);
			brandText.Controls.Add(new Label
			{
				Text = "批量管理美客多促销与折扣活动",
				Dock = DockStyle.Fill,
				TextAlign = ContentAlignment.TopLeft,
				Font = new Font("Microsoft YaHei UI", 9f, FontStyle.Regular),
				ForeColor = UiTheme.MutedText,
				BackColor = Color.Transparent,
				Margin = new Padding(0)
			}, 0, 1);
			brand.Controls.Add(brandText, 1, 0);
			layout.Controls.Add(brand, 0, 0);

			FlowLayoutPanel navigation = new FlowLayoutPanel
			{
				Dock = DockStyle.Fill,
				FlowDirection = FlowDirection.LeftToRight,
				WrapContents = false,
				Padding = new Padding(0, 7, 0, 7),
				BackColor = Color.Transparent,
				Margin = new Padding(0)
			};
			RoundedButton workbench = new RoundedButton();
			RoundedButton history = new RoundedButton();
			RoundedButton activities = new RoundedButton();
			ConfigureNavigationButton(workbench, "工作台", 96, selected: true);
			ConfigureNavigationButton(history, "批次历史", 104, selected: false);
			ConfigureNavigationButton(activities, "活动管理", 104, selected: false);
			Button[] navigationButtons = new Button[4] { workbench, history, activities, _settingsButton };
			void SelectNavigation(Button selected)
			{
				foreach (Button button in navigationButtons)
				{
					if (button is RoundedButton rounded)
					{
						rounded.Selected = button == selected;
						rounded.Invalidate();
					}
				}
			}
			workbench.Click += delegate
			{
				SelectNavigation(workbench);
				_modeSelect.Focus();
			};
			history.Click += delegate
			{
				SelectNavigation(history);
				_taskGrid.Focus();
			};
			activities.Click += delegate
			{
				SelectNavigation(activities);
				_sellerActivitySelect.Focus();
			};
			_settingsButton.Click += delegate
			{
				SelectNavigation(_settingsButton);
			};
			navigation.Controls.Add(workbench);
			navigation.Controls.Add(history);
			navigation.Controls.Add(activities);
			navigation.Controls.Add(_settingsButton);
			layout.Controls.Add(navigation, 1, 0);
			header.Controls.Add(layout);
			return header;
		}

		private RoundedPanel BuildControlSurface()
		{
			RoundedPanel surface = new RoundedPanel
			{
				Dock = DockStyle.Fill,
				Padding = new Padding(10),
				FillColor = UiTheme.CardBackground,
				BorderColor = UiTheme.GoldBorder
			};
			TableLayoutPanel layout = new TableLayoutPanel
			{
				Dock = DockStyle.Fill,
				ColumnCount = 1,
				RowCount = 4,
				AutoScroll = true,
				BackColor = Color.Transparent,
				Margin = new Padding(0)
			};
			layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 214f));
			layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 158f));
			layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100f));
			layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 110f));

			TableLayoutPanel scope = new TableLayoutPanel
			{
				Dock = DockStyle.Fill,
				ColumnCount = 1,
				RowCount = 3,
				BackColor = Color.Transparent,
				Margin = new Padding(12, 0, 12, 10)
			};
			scope.RowStyles.Add(new RowStyle(SizeType.Percent, 33.33f));
			scope.RowStyles.Add(new RowStyle(SizeType.Percent, 33.33f));
			scope.RowStyles.Add(new RowStyle(SizeType.Percent, 33.34f));
			scope.Controls.Add(CreateLabeledField("模式", _modeSelect), 0, 0);
			scope.Controls.Add(CreateLabeledField("店铺", _accountSelect), 0, 1);
			scope.Controls.Add(CreateLabeledField("站点", _siteSelect), 0, 2);
			RoundedPanel scopeSurface = BuildControlSection("执行范围", scope);
			scopeSurface.Margin = new Padding(0, 0, 0, 8);
			layout.Controls.Add(scopeSurface, 0, 0);

			TableLayoutPanel activity = new TableLayoutPanel
			{
				Dock = DockStyle.Fill,
				ColumnCount = 1,
				RowCount = 2,
				BackColor = Color.Transparent,
				Margin = new Padding(12, 0, 12, 10)
			};
			activity.RowStyles.Add(new RowStyle(SizeType.Percent, 50f));
			activity.RowStyles.Add(new RowStyle(SizeType.Percent, 50f));
			activity.Controls.Add(CreateActivityField("自建活动", _sellerActivitySelect, _sellerDiscount), 0, 0);
			activity.Controls.Add(CreateActivityField("官方活动", _officialActivitySelect, _officialDiscount), 0, 1);
			RoundedPanel activitySurface = BuildControlSection("活动参数", activity);
			activitySurface.Margin = new Padding(0, 0, 0, 8);
			layout.Controls.Add(activitySurface, 0, 1);

			TableLayoutPanel decision = new TableLayoutPanel
			{
				Dock = DockStyle.Fill,
				ColumnCount = 1,
				RowCount = 2,
				BackColor = Color.Transparent,
				Margin = new Padding(12, 0, 12, 10)
			};
			decision.RowStyles.Add(new RowStyle(SizeType.Percent, 100f));
			decision.RowStyles.Add(new RowStyle(SizeType.Absolute, 24f));
			_todayLabel.Dock = DockStyle.Fill;
			_todayLabel.AutoEllipsis = true;
			_todayLabel.BackColor = Color.Transparent;
			_todayLabel.ForeColor = UiTheme.MainText;
			_todayLabel.TextAlign = ContentAlignment.MiddleLeft;
			_todayLabel.Font = new Font("Microsoft YaHei UI", 9.5f, FontStyle.Regular);
			_todayLabel.Text = "等待判断。提交执行前请核对店铺、站点、活动和折扣。";
			_todayLabel.Margin = new Padding(0);
			decision.Controls.Add(_todayLabel, 0, 0);
			_statusLabel.Dock = DockStyle.Fill;
			_statusLabel.AutoEllipsis = true;
			_statusLabel.BackColor = Color.Transparent;
			_statusLabel.ForeColor = UiTheme.WeakText;
			_statusLabel.TextAlign = ContentAlignment.MiddleLeft;
			_statusLabel.Font = new Font("Microsoft YaHei UI", 8.5f, FontStyle.Regular);
			_statusLabel.Margin = new Padding(0);
			decision.Controls.Add(_statusLabel, 0, 1);
			RoundedPanel decisionSurface = BuildControlSection("今日判断", decision);
			decisionSurface.Margin = new Padding(0, 0, 0, 8);
			layout.Controls.Add(decisionSurface, 0, 2);

			TableLayoutPanel actions = new TableLayoutPanel
			{
				Dock = DockStyle.Bottom,
				ColumnCount = 1,
				RowCount = 2,
				Height = 100,
				BackColor = Color.Transparent,
				Margin = new Padding(0, 10, 0, 0)
			};
			actions.RowStyles.Add(new RowStyle(SizeType.Percent, 50f));
			actions.RowStyles.Add(new RowStyle(SizeType.Percent, 50f));
			_submitButton.Margin = new Padding(0, 0, 0, 5);
			_refreshTasksButton.Margin = new Padding(0, 5, 0, 0);
			actions.Controls.Add(_submitButton, 0, 0);
			actions.Controls.Add(_refreshTasksButton, 0, 1);
			layout.Controls.Add(actions, 0, 3);
			surface.Controls.Add(layout);
			return surface;
		}

		private static RoundedPanel BuildControlSection(string title, Control content)
		{
			RoundedPanel section = new RoundedPanel
			{
				Dock = DockStyle.Fill,
				Padding = new Padding(1),
				FillColor = UiTheme.CardBackground,
				BorderColor = UiTheme.GoldBorder,
				CornerRadius = 8
			};
			TableLayoutPanel sectionLayout = new TableLayoutPanel
			{
				Dock = DockStyle.Fill,
				ColumnCount = 1,
				RowCount = 2,
				BackColor = Color.Transparent,
				Margin = new Padding(0)
			};
			sectionLayout.RowStyles.Add(new RowStyle(SizeType.Absolute, 38f));
			sectionLayout.RowStyles.Add(new RowStyle(SizeType.Percent, 100f));
			Label heading = CreateSectionTitle(title);
			heading.Padding = new Padding(12, 0, 0, 0);
			sectionLayout.Controls.Add(heading, 0, 0);
			sectionLayout.Controls.Add(content, 0, 1);
			section.Controls.Add(sectionLayout);
			return section;
		}

		private static RoundedPanel BuildTitledSurface(string title, Control content)
		{
			RoundedPanel surface = new RoundedPanel
			{
				Dock = DockStyle.Fill,
				Padding = new Padding(1),
				FillColor = UiTheme.CardBackground,
				BorderColor = UiTheme.GoldBorder
			};
			TableLayoutPanel layout = new TableLayoutPanel
			{
				Dock = DockStyle.Fill,
				ColumnCount = 1,
				RowCount = 2,
				BackColor = Color.Transparent,
				Margin = new Padding(0)
			};
			layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 46f));
			layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100f));
			layout.Controls.Add(new Label
			{
				Text = title,
				Dock = DockStyle.Fill,
				TextAlign = ContentAlignment.MiddleLeft,
				Padding = new Padding(14, 0, 0, 0),
				Margin = new Padding(0),
				Font = new Font("Microsoft YaHei UI", 11f, FontStyle.Bold),
				ForeColor = UiTheme.MainText,
				BackColor = Color.Transparent
			}, 0, 0);
			layout.Controls.Add(content, 0, 1);
			surface.Controls.Add(layout);
			return surface;
		}

		private static Control CreateLabeledField(string label, Control control)
		{
			TableLayoutPanel field = new TableLayoutPanel
			{
				Dock = DockStyle.Fill,
				ColumnCount = 1,
				RowCount = 2,
				BackColor = Color.Transparent,
				Margin = new Padding(0, 0, 0, 6)
			};
			field.RowStyles.Add(new RowStyle(SizeType.Absolute, 23f));
			field.RowStyles.Add(new RowStyle(SizeType.Percent, 100f));
			field.Controls.Add(CreateFieldLabel(label), 0, 0);
			field.Controls.Add(CreateInputHost(control), 0, 1);
			return field;
		}

		private static Control CreateActivityField(string label, Control activity, Control discount)
		{
			TableLayoutPanel field = new TableLayoutPanel
			{
				Dock = DockStyle.Fill,
				ColumnCount = 2,
				RowCount = 2,
				BackColor = Color.Transparent,
				Margin = new Padding(0, 0, 0, 6)
			};
			field.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100f));
			field.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 72f));
			field.RowStyles.Add(new RowStyle(SizeType.Absolute, 23f));
			field.RowStyles.Add(new RowStyle(SizeType.Percent, 100f));
			Label fieldLabel = CreateFieldLabel(label);
			field.Controls.Add(fieldLabel, 0, 0);
			field.SetColumnSpan(fieldLabel, 2);
			RoundedPanel activityHost = CreateInputHost(activity);
			activityHost.Margin = new Padding(0, 0, 8, 0);
			field.Controls.Add(activityHost, 0, 1);
			field.Controls.Add(CreateInputHost(discount), 1, 1);
			return field;
		}

		private static RoundedPanel CreateInputHost(Control control)
		{
			RoundedPanel host = new RoundedPanel
			{
				Dock = DockStyle.Fill,
				Padding = new Padding(1),
				Margin = new Padding(0),
				CornerRadius = 6,
				FillColor = UiTheme.InputBackground,
				BorderColor = UiTheme.GoldBorder
			};
			control.BackColor = UiTheme.InputBackground;
			control.ForeColor = UiTheme.MainText;
			if (control is ComboBox combo)
			{
				combo.Dock = DockStyle.Fill;
				combo.Margin = new Padding(0);
				host.Click += delegate
				{
					if (combo.Enabled)
					{
						combo.Focus();
						combo.DroppedDown = true;
					}
				};
				host.Controls.Add(combo);
			}
			else
			{
				control.Dock = DockStyle.Fill;
				control.Margin = new Padding(6, 4, 4, 3);
				if (control is NumericUpDown number)
				{
					number.BorderStyle = BorderStyle.None;
				}
				host.Controls.Add(control);
			}
			control.Enter += delegate
			{
				host.BorderColor = UiTheme.GoldFocus;
				host.Invalidate();
			};
			control.Leave += delegate
			{
				host.BorderColor = UiTheme.GoldBorder;
				host.Invalidate();
			};
			return host;
		}

		private static Label CreateFieldLabel(string text)
		{
			return new Label
			{
				Text = text,
				Dock = DockStyle.Fill,
				TextAlign = ContentAlignment.MiddleLeft,
				Font = new Font("Microsoft YaHei UI", 9f, FontStyle.Regular),
				ForeColor = UiTheme.MutedText,
				BackColor = Color.Transparent,
				Margin = new Padding(0)
			};
		}

		private static Label CreateSectionTitle(string text)
		{
			return new Label
			{
				Text = text,
				Dock = DockStyle.Fill,
				TextAlign = ContentAlignment.MiddleLeft,
				Font = new Font("Microsoft YaHei UI", 10.5f, FontStyle.Bold),
				ForeColor = ColorTranslator.FromHtml("#D9E5D5"),
				BackColor = Color.Transparent,
				Margin = new Padding(0)
			};
		}

		private static Panel CreateDivider()
		{
			return new Panel
			{
				Dock = DockStyle.Top,
				Height = 1,
				BackColor = UiTheme.NormalBorder,
				Margin = new Padding(0, 11, 0, 11)
			};
		}

		private static void ConfigureNavigationButton(Button button, string text, int width, bool selected)
		{
			button.Text = text;
			button.Width = width;
			button.Height = 36;
			button.Font = new Font("Microsoft YaHei UI", 9.5f, FontStyle.Regular);
			button.Margin = new Padding(0, 0, 8, 0);
			if (button is RoundedButton rounded)
			{
				rounded.Selected = selected;
				rounded.CornerRadius = 8;
				rounded.BackColor = selected ? UiTheme.SelectedGreen : UiTheme.NavigationBackground;
				rounded.ForeColor = selected ? UiTheme.ButtonText : UiTheme.MutedText;
				rounded.BorderColor = selected ? UiTheme.GreenBorder : UiTheme.NormalBorder;
				rounded.HoverColor = UiTheme.HoverBackground;
				rounded.PressedColor = UiTheme.SelectedGreen;
			}
			else
			{
				UiTheme.StyleButton(button, primary: false);
			}
		}

		private async Task InitializeWorkbenchAsync()
		{
			_ = 2;
			bool ready = false;
			try
			{
				SetBusy(busy: true, WorkbenchPreparingText);
				await CheckHealthAsync();
				await LoadSettingsAsync();
				await LoadAccountsAsync(verifyAccounts: false);
				ready = true;
				SetBusy(busy: false, WorkbenchReadyText);
				_submitButton.Focus();
				_ = LoadStartupDataAsync();
				Log("工作台已打开。");
			}
			catch (Exception ex)
			{
				string message = ProductFacingErrorMessage(ex);
				Log("工作台准备失败：" + message);
				AppendInternalDiagnostic("UI initialize failed", ex);
				MessageBox.Show(message, "美客多折扣管家", MessageBoxButtons.OK, MessageBoxIcon.Hand);
			}
			finally
			{
				SetBusy(busy: false, ready ? WorkbenchReadyText : "工作台未准备好");
				_submitButton.Focus();
			}
		}

		private async Task LoadStartupDataAsync()
		{
			try
			{
				await LoadGlobalTodayDiscountAsync();
				await Task.WhenAll(RefreshTasksAsync(), RefreshActivitiesAsync(writeLog: false));
				_autoDecisionDataReady = true;
				await QueueAutoDecisionRefreshAsync(immediate: true);
				Log("历史记录和活动下拉已加载。");
			}
			catch (Exception ex)
			{
				Log("启动后台加载未完成：" + ProductFacingErrorMessage(ex));
				AppendInternalDiagnostic("startup background load failed", ex);
			}
		}

		private async Task CheckHealthAsync()
		{
			using JsonDocument doc = await GetJsonAsync("/api/health");
			if (!Bool(doc.RootElement, "ok"))
			{
				throw new InvalidOperationException("程序组件还没有准备好。");
			}
		}

		private async Task LoadSettingsAsync()
		{
			using JsonDocument doc = await GetJsonAsync("/api/settings");
			if (!doc.RootElement.TryGetProperty("settings", out var settings))
			{
				return;
			}
			_authDir = StringValue(settings, "authDir", "C:\\Users\\dztf6\\Documents\\美客多授权");
			_outputDir = StringValue(settings, "outputDir", "");
			_sellerDiscount.Value = DecimalValue(settings, "sellerDefaultDiscount", 5m);
			_officialDiscount.Value = DecimalValue(settings, "officialDefaultDiscount", 6m);
			_readConcurrency = DecimalValue(settings, "readConcurrency", 2m);
			_previewConcurrency = DecimalValue(settings, "previewConcurrency", 2m);
			_writeConcurrency = DecimalValue(settings, "writeConcurrency", 2m);
			_storeAliases.Clear();
			if (settings.TryGetProperty("storeAliases", out var aliases) && aliases.ValueKind == JsonValueKind.Object)
			{
				foreach (JsonProperty property in aliases.EnumerateObject())
				{
					string alias = property.Value.ToString().Trim();
					if (property.Name.Length > 0 && alias.Length > 0)
					{
						_storeAliases[property.Name] = alias;
					}
				}
			}
			_operatingSites.Clear();
			if (settings.TryGetProperty("operatingSites", out var operatingSites) && operatingSites.ValueKind == JsonValueKind.Object)
			{
				foreach (JsonProperty property2 in operatingSites.EnumerateObject())
				{
					if (property2.Value.ValueKind != JsonValueKind.Array)
					{
						continue;
					}
					_operatingSites[property2.Name] = property2.Value.EnumerateArray()
						.Select((JsonElement value) => value.ToString().Trim().ToUpperInvariant())
						.Where((string value) => value.Length > 0)
						.Distinct(StringComparer.OrdinalIgnoreCase)
						.OrderBy((string value) => value)
						.ToList();
				}
			}
		}

		private async Task LoadAccountsAsync(bool verifyAccounts = true)
		{
			using JsonDocument doc = await GetJsonAsync("/api/accounts");
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
				foreach (JsonElement account in accounts.EnumerateArray())
				{
					string id = StringValue(account, "account_id", "");
					if (id.Length != 0)
					{
						if (verifyAccounts)
						{
							List<AccountInfo> accounts2 = _accounts;
							accounts2.Add(await LoadVerifiedAccountInfoAsync(account, id));
						}
						else
						{
							string display = AccountDisplayName(account, id);
							_accounts.Add(new AccountInfo(id, display, StringValue(account, "site_id", ""), StoreNameForAccount(id, display)));
						}
					}
				}
				BuildStoreItems();
				if (_accountSelect.Items.Count > 0)
				{
					_accountSelect.SelectedIndex = 0;
					_selectedStoreKey = ((ComboItem)_accountSelect.SelectedItem).Value;
					_accountId = ResolveAccountIdForStore(_selectedStoreKey);
					Log("已读取店铺列表：" + StoreListText() + "。授权账号：" + AuthorizedAccountsText());
				}
			}
			finally
			{
				_updatingSelectors = false;
			}
		}

		private async Task<AccountInfo> LoadVerifiedAccountInfoAsync(JsonElement account, string accountId)
		{
			string display = AccountDisplayName(account, accountId);
			string site = StringValue(account, "site_id", "");
			try
			{
				using ApiJson doc = await PostJsonAsync("/api/accounts/" + Uri.EscapeDataString(accountId) + "/verify", new { });
				if (doc.Root.TryGetProperty("account", out var verified))
				{
					display = AccountDisplayName(verified, accountId);
					site = StringValue(verified, "site_id", site);
				}
			}
			catch (Exception ex)
			{
				Log("授权账号 " + accountId + " 昵称验证未完成，已使用本地信息：" + ex.Message);
			}
			return new AccountInfo(accountId, display, site, StoreNameForAccount(accountId, display));
		}

		private string StoreNameForAccount(string accountId, string display)
		{
			if (_storeAliases.TryGetValue(accountId, out string? alias) && alias.Trim().Length > 0)
			{
				return alias.Trim();
			}
			string knownStore = KnownStoreNameForAccountId(accountId);
			if (knownStore.Length <= 0)
			{
				return InferStoreName(display);
			}
			return knownStore;
		}

		private void BuildStoreItems()
		{
			_accountSelect.Items.Clear();
			if (_accounts.Count == 0)
			{
				return;
			}
			_storeAccountIds["all"] = _accounts.Select((AccountInfo account) => account.AccountId).Distinct().ToList();
			_accountSelect.Items.Add(new ComboItem("all", "全部店铺"));
			foreach (IGrouping<string, AccountInfo> group2 in from account in _accounts
				group account by account.StoreName into @group
				orderby @group.Key
				select @group)
			{
				string storeKey = group2.Key;
				_storeAccountIds[storeKey] = group2.Select((AccountInfo account) => account.AccountId).Distinct().ToList();
				_accountSelect.Items.Add(new ComboItem(storeKey, storeKey));
			}
		}

		private async Task AccountChangedAsync()
		{
			if (!_updatingSelectors && _accountSelect.SelectedItem is ComboItem item)
			{
				_selectedStoreKey = item.Value;
				_accountId = ResolveAccountIdForStore(_selectedStoreKey);
				await RefreshActivitiesAsync(writeLog: false);
				await RefreshTasksAsync();
				await QueueAutoDecisionRefreshAsync();
			}
		}

		private async Task RefreshActivitiesAsync(bool writeLog)
		{
			IReadOnlyList<string> accountIds = SelectedAccountIds();
			if (accountIds.Count == 0)
			{
				return;
			}
			string currentSite = SelectedValue(_siteSelect);
			_updatingSelectors = true;
			try
			{
				_siteSelect.Items.Clear();
				_siteSelect.Items.Add(new ComboItem("", "全部站点"));
				List<string> siteNotes = new List<string>();
				HashSet<string> addedSiteIds = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
				foreach (string accountId in accountIds)
				{
					using JsonDocument sitesDoc = await GetJsonAsync("/api/accounts/" + Uri.EscapeDataString(accountId) + "/sites");
					if (!sitesDoc.RootElement.TryGetProperty("sites", out var sites) || sites.ValueKind != JsonValueKind.Array)
					{
						continue;
					}
					foreach (JsonElement site in sites.EnumerateArray())
					{
						string id = StringValue(site, "site_id", "");
						if (id.Length != 0)
						{
							int total = IntValue(site, "total", 0);
							string display = SiteDisplayName(id);
							if (addedSiteIds.Add(id))
							{
								_siteSelect.Items.Add(new ComboItem(id, display));
							}
							StringValue(site, "last_promotion_status", "");
							string error = StringValue(site, "last_error", "");
							string logistics = StringValue(site, "logistic_type", "");
							string logisticsText = ((logistics.Length > 0) ? ("[" + logistics + "]") : "");
							string storeName = StoreNameForAccountId(accountId);
							siteNotes.Add((error.Length > 0) ? $"{storeName}-{display}{logisticsText}读取失败：{error}" : $"{storeName}-{display}{logisticsText}{total}个活动");
						}
					}
				}
				if (writeLog && siteNotes.Count > 0)
				{
					Log("站点活动：" + string.Join("，", siteNotes));
				}
				SelectComboByValue(_siteSelect, currentSite);
				if (_siteSelect.SelectedIndex < 0)
				{
					_siteSelect.SelectedIndex = 0;
				}
			}
			finally
			{
				_updatingSelectors = false;
			}
			string query = ((SelectedValue(_siteSelect).Length > 0) ? ("?siteId=" + Uri.EscapeDataString(SelectedValue(_siteSelect))) : "");
			_sellerActivitySelect.Items.Clear();
			_officialActivitySelect.Items.Clear();
			_sellerActivitySelect.Items.Add(new ComboItem("", "全部自建活动"));
			_sellerActivitySelect.Items.Add(new ComboItem(ExcludeActivityValue, "不处理自建活动"));
			_officialActivitySelect.Items.Add(new ComboItem("", "全部官方活动"));
			_officialActivitySelect.Items.Add(new ComboItem(ExcludeActivityValue, "不处理官方活动"));
			int totalPromotions = 0;
			Dictionary<string, ActivityChoice> sellerNames = new Dictionary<string, ActivityChoice>(StringComparer.OrdinalIgnoreCase);
			Dictionary<string, ActivityChoice> officialNames = new Dictionary<string, ActivityChoice>(StringComparer.OrdinalIgnoreCase);
			foreach (string accountId2 in accountIds)
			{
				using JsonDocument promosDoc = await GetJsonAsync("/api/accounts/" + Uri.EscapeDataString(accountId2) + "/promotions" + query);
				if (!promosDoc.RootElement.TryGetProperty("promotions", out var promotions) || promotions.ValueKind != JsonValueKind.Array)
				{
					continue;
				}
				foreach (JsonElement item in promotions.EnumerateArray())
				{
					totalPromotions++;
					string type = StringValue(item, "promotion_type", "");
					string displayName = ActivityDisplayName(item);
					if (displayName.Length == 0)
					{
						continue;
					}
					string key = NormalizeActivityNameKey(displayName);
					if (key.Length != 0)
					{
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
			}
			foreach (ActivityChoice choice3 in sellerNames.Values.OrderBy<ActivityChoice, string>((ActivityChoice value) => value.DisplayName, StringComparer.OrdinalIgnoreCase))
			{
				_sellerActivitySelect.Items.Add(new ComboItem(choice3.Key, choice3.DisplayName));
			}
			foreach (ActivityChoice choice2 in officialNames.Values.OrderBy<ActivityChoice, string>((ActivityChoice value) => value.DisplayName, StringComparer.OrdinalIgnoreCase))
			{
				_officialActivitySelect.Items.Add(new ComboItem(choice2.Key, choice2.DisplayName));
			}
			_sellerActivitySelect.SelectedIndex = 0;
			_officialActivitySelect.SelectedIndex = 0;
			UpdateComboDropDownWidth(_sellerActivitySelect);
			UpdateComboDropDownWidth(_officialActivitySelect);
			if (writeLog)
			{
				Log($"活动已刷新：当前筛选 {totalPromotions} 个。");
			}
			string[] duplicateNotes = (from choice in sellerNames.Values
				where choice.Count > 1
				select $"自建活动“{choice.DisplayName}”匹配 {choice.Count} 个活动").Concat(from choice in officialNames.Values
				where choice.Count > 1
				select $"官方活动“{choice.DisplayName}”匹配 {choice.Count} 个活动").ToArray();
			if (writeLog && duplicateNotes.Length != 0)
			{
				Log("同名活动：" + string.Join("，", duplicateNotes));
			}
		}

		private async Task LoadActivitiesFromApiAsync()
		{
			IReadOnlyList<string> accountIds = SelectedAccountIds();
			if (accountIds.Count == 0)
			{
				return;
			}
			await RunUiTaskAsync("加载活动", async delegate
			{
				int totalAll = 0;
				foreach (string accountId in accountIds)
				{
					using ApiJson doc = await PostJsonAsync("/api/accounts/" + Uri.EscapeDataString(accountId) + "/promotions/fetch", new { });
					int total = IntValue(doc.Root, "total", 0);
					totalAll += total;
					Log($"加载活动完成：{StoreNameForAccountId(accountId)} {total} 个。");
					if (doc.Root.TryGetProperty("children", out var children) && children.ValueKind == JsonValueKind.Array)
					{
						foreach (JsonElement item in children.EnumerateArray())
						{
							string site = SiteDisplayName(StringValue(item, "site_id", ""));
							string logistics = StringValue(item, "logistic_type", "");
							int count = IntValue(item, "total", 0);
							string status = StringValue(item, "status", "ok");
							string error = StringValue(item, "error", "");
							Log((error.Length > 0) ? $"  {site}[{logistics}] 活动读取失败：{error}" : $"  {site}[{logistics}] 活动 {count} 个，状态 {status}。");
						}
					}
				}
				Log($"加载活动完成：合计 {totalAll} 个。");
				await RefreshActivitiesAsync(writeLog: false);
			});
		}

		private async Task DecideTodayAsync()
		{
			if (_accountId.Length == 0)
			{
				return;
			}
			await RunUiTaskAsync("判断今日", async delegate
			{
				using ApiJson doc = await PostJsonAsync("/api/today/decision", new
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
			if (_accountId.Length == 0)
			{
				return;
			}
			await RunUiTaskAsync("预览今日", async delegate
			{
				using ApiJson doc = await PostJsonAsync("/api/today/preview", new
				{
					accountId = _accountId,
					filters = BuildFilters(),
					priceMode = "discount",
					sellerDiscountPercent = _sellerDiscount.Value,
					officialDiscountPercent = _officialDiscount.Value
				});
				if (doc.Root.TryGetProperty("decision", out var decision))
				{
					RenderTodayDecision(decision);
				}
				Log("预览今日完成。");
				await RefreshTasksAsync();
			});
		}

		private async Task QueueAutoDecisionRefreshAsync(bool immediate = false)
		{
			int version = Interlocked.Increment(ref _autoDecisionRefreshVersion);
			if (!string.IsNullOrWhiteSpace(SelectedSubmitAction()))
			{
				_autoResolvedAction = "";
				UpdateDiscountInputState();
				_todayLabel.Text = $"今日判断：当前为{SelectedSubmitModeText()}，使用界面中的手动设置。";
				return;
			}
			ApplyGlobalTodayDiscount();
			if (!_autoDecisionDataReady)
			{
				_todayLabel.Text = GlobalTodayDiscountSummary() + " 当前范围正在等待店铺、站点和活动数据加载。";
				return;
			}
			if (_executionJobRunning)
			{
				return;
			}
			if (!immediate)
			{
				await Task.Delay(180);
			}
			if (version != _autoDecisionRefreshVersion || !string.IsNullOrWhiteSpace(SelectedSubmitAction()))
			{
				return;
			}
			IReadOnlyList<string> accountIds = SelectedAccountIds();
			if (accountIds.Count == 0)
			{
				_todayLabel.Text = GlobalTodayDiscountSummary() + " 当前店铺没有可用授权账号。";
				return;
			}
			_todayLabel.Text = GlobalTodayDiscountSummary() + " 正在判断当前店铺、站点和活动范围的执行动作...";
			try
			{
				ResolvedSubmitDecision resolved = await ResolveGlobalSubmitActionAsync(accountIds, "", writeConflictLogs: false);
				if (version != _autoDecisionRefreshVersion || !string.IsNullOrWhiteSpace(SelectedSubmitAction()))
				{
					return;
				}
				_autoResolvedAction = resolved.Action;
				UpdateDiscountInputState();
				string scope = $"{SelectedComboText(_accountSelect)} / {SelectedComboText(_siteSelect)}";
				_todayLabel.Text = string.Equals(resolved.Action, "cancel", StringComparison.OrdinalIgnoreCase)
					? $"{GlobalTodayDiscountSummary()} 当前范围：{scope} 应执行{LegacyActionText(resolved.Action)}，取消不使用折扣。"
					: $"{GlobalTodayDiscountSummary()} 当前范围：{scope} 应执行{LegacyActionText(resolved.Action)}。";
			}
			catch (Exception ex)
			{
				if (version != _autoDecisionRefreshVersion || !string.IsNullOrWhiteSpace(SelectedSubmitAction()))
				{
					return;
				}
				_autoResolvedAction = "";
				UpdateDiscountInputState();
				_todayLabel.Text = GlobalTodayDiscountSummary() + " 当前范围：" + FriendlyExecutionErrorMessage(ex);
			}
		}

		private async Task LoadGlobalTodayDiscountAsync()
		{
			try
			{
				using JsonDocument doc = await GetJsonAsync("/api/today/global-discount");
				if (!doc.RootElement.TryGetProperty("discount", out var discount) || discount.ValueKind != JsonValueKind.Object)
				{
					throw new InvalidOperationException("今日折扣记录不完整。");
				}
				_globalTodaySellerDiscount = DecimalValue(discount, "seller_discount", _sellerDiscount.Value);
				_globalTodayOfficialDiscount = DecimalValue(discount, "official_discount", _officialDiscount.Value);
				_globalTodayDiscountMessage = StringValue(discount, "message", "");
				_globalTodayDiscountReady = true;
			}
			catch (Exception ex)
			{
				_globalTodaySellerDiscount = _sellerDiscount.Value;
				_globalTodayOfficialDiscount = _officialDiscount.Value;
				_globalTodayDiscountMessage = "未读取到可用更新历史，已使用保存设置。";
				_globalTodayDiscountReady = true;
				AppendInternalDiagnostic("global today discount load failed", ex);
			}
			if (string.IsNullOrWhiteSpace(SelectedSubmitAction()))
			{
				ApplyGlobalTodayDiscount();
				if (!_autoDecisionDataReady)
				{
					_todayLabel.Text = GlobalTodayDiscountSummary() + " 当前范围正在等待店铺、站点和活动数据加载。";
				}
			}
		}

		private void ApplyGlobalTodayDiscount()
		{
			if (!_globalTodayDiscountReady || !string.IsNullOrWhiteSpace(SelectedSubmitAction()))
			{
				return;
			}
			_sellerDiscount.Value = ClampDiscountValue(_globalTodaySellerDiscount);
			_officialDiscount.Value = ClampDiscountValue(_globalTodayOfficialDiscount);
		}

		private string GlobalTodayDiscountSummary()
		{
			if (!_globalTodayDiscountReady)
			{
				return "今日折扣：正在读取更新周期。";
			}
			string summary = $"今日折扣：自建{_globalTodaySellerDiscount:0}%，官方{_globalTodayOfficialDiscount:0}%。";
			if (_globalTodayDiscountMessage.Contains("未找到", StringComparison.OrdinalIgnoreCase) || _globalTodayDiscountMessage.Contains("未读取到", StringComparison.OrdinalIgnoreCase))
			{
				return summary + " " + _globalTodayDiscountMessage;
			}
			return summary;
		}

		private async Task SubmitExecutionAsync()
		{
			if (_accountId.Length == 0)
			{
				return;
			}
			await SubmitExecutionJobWrapperAsync();
		}

		private async Task SubmitExecutionJobWrapperAsync()
		{
			try
			{
				SetExecutionBusy(busy: true);
				string selectedAction = SelectedSubmitAction();
				IReadOnlyList<string> accountIds = SelectedAccountIds();
				if (accountIds.Count == 0)
				{
					throw new InvalidOperationException("当前店铺没有可用授权账号。");
				}
				if (string.IsNullOrWhiteSpace(selectedAction))
				{
					if (!_globalTodayDiscountReady)
					{
						await LoadGlobalTodayDiscountAsync();
					}
					ApplyGlobalTodayDiscount();
					Log($"自动判断今日折扣：自建{_sellerDiscount.Value:0}%，官方{_officialDiscount.Value:0}%。");
				}
				ResolvedSubmitDecision resolvedSubmit = await ResolveGlobalSubmitActionAsync(accountIds, selectedAction);
				string action = resolvedSubmit.Action;
				using (StyledConfirmDialog confirm = new StyledConfirmDialog("最终执行确认", BuildExecutionConfirmationText(action), "确认执行", "取消"))
				{
					if (confirm.ShowDialog(this) != DialogResult.OK)
					{
						Log("提交执行已取消，未创建执行任务。");
						return;
					}
				}
				Log("本次执行动作：" + LegacyActionText(action) + "。");
				Log(ExecutionStartLogText(action));
				if (string.Equals(action, "enroll", StringComparison.OrdinalIgnoreCase) && !(await EnsureSellerCampaignCreationGuideAsync(accountIds)))
				{
					return;
				}
				Log("加载店铺站点列表...");
				(int Total, int Active, int Inactive) shopSites = await CountSelectedStoreSitesAsync(accountIds);
				Log("店铺站点列表完成");
				Log($"店铺站点数：{shopSites.Total}");
				Log($"已加载店铺站点：{shopSites.Total} 个");
				Log($"其中当前有活动：{shopSites.Active} 个，未开放/未读取到活动：{shopSites.Inactive} 个");
				(int Concurrency, string Source) readPlan = await ResolveReadConcurrencyPlanAsync();
				(int, string) writePlan = await ResolveWriteConcurrencyPlanAsync();
				int siteConcurrency = Math.Max(1, Math.Min(readPlan.Concurrency, accountIds.Count));
				int activityConcurrency = Math.Max(1, Math.Min(readPlan.Concurrency, 20));
				var (perJobWriteConcurrency, _) = writePlan;
				Log($"并发依据：读取并发建议={readPlan.Source}；写入并发建议={writePlan.Item2}。");
				Log($"并发处理店铺站点：{shopSites.Total} 个，站点并发={siteConcurrency}，活动并发={activityConcurrency}，商品写入并发={perJobWriteConcurrency}。");
				string[] queuedStoreNames = (from name in accountIds.Select(StoreNameForAccountId)
					where name.Length > 0
					select name).Distinct<string>(StringComparer.OrdinalIgnoreCase).ToArray();
				Log($"展开店铺任务：{accountIds.Count} 个（{string.Join("、", queuedStoreNames)}）。");
				foreach (string accountId2 in accountIds)
				{
					Log(StoreNameForAccountId(accountId2) + "：已加入执行队列。");
				}
				SemaphoreSlim semaphore = new SemaphoreSlim(siteConcurrency);
				try
				{
					ExecutionOutcome[] outcomes = await Task.WhenAll(accountIds.Select(async delegate(string accountId, int index)
					{
						string storeName = StoreNameForAccountId(accountId);
						if (index >= siteConcurrency)
						{
							Log(storeName + "：等待并发槽位。");
						}
						await semaphore.WaitAsync();
						try
						{
							Log(storeName + "：开始店铺任务。");
							ExecutionOutcome outcome = await StartAndPollExecutionJobAsync(accountId, action, perJobWriteConcurrency, siteConcurrency, activityConcurrency, readPlan.Concurrency);
							Log(StoreActionSummaryText(outcome));
							Log(storeName + "：店铺任务完成。");
							return outcome;
						}
						catch (Exception ex2)
						{
							Log(storeName + "：店铺任务未完整完成：" + FriendlyExecutionErrorMessage(ex2));
							throw;
						}
						finally
						{
							semaphore.Release();
						}
					}).ToArray());
					Log(OverallActionSummaryText(action, outcomes));
					await RefreshTasksAsync();
				}
				finally
				{
					if (semaphore != null)
					{
						((IDisposable)semaphore).Dispose();
					}
				}
			}
			catch (Exception ex)
			{
				string message = FriendlyExecutionErrorMessage(ex);
				Log("提交执行未完整完成：" + message);
				if (!IsBusinessTimeoutOrRequestError(ex))
				{
					MessageBox.Show(message, "美客多折扣管家", MessageBoxButtons.OK, MessageBoxIcon.Asterisk);
				}
			}
			finally
			{
				_currentExecutionJobId = "";
				lock (_currentExecutionJobIds)
				{
					_currentExecutionJobIds.Clear();
				}
				SetExecutionBusy(busy: false);
			}
		}

		private async Task<bool> EnsureSellerCampaignCreationGuideAsync(IReadOnlyList<string> accountIds)
		{
			if (SelectedValue(_sellerActivitySelect).Length > 0)
			{
				return true;
			}
			using ApiJson scopeDoc = await PostJsonAsync("/api/promotion-creation/seller-campaign/batch-precheck", new
			{
				accountIds = accountIds,
				filters = BuildFilters()
			});
			if (!Bool(scopeDoc.Root, "ok"))
			{
				throw new InvalidOperationException(StringValue(scopeDoc.Root, "error", "自建活动检查失败。"));
			}
			int existingCount = IntValue(scopeDoc.Root, "existing_count", 0);
			int unknownCount = IntValue(scopeDoc.Root, "unknown_not_returned_count", 0);
			int unreadableCount = IntValue(scopeDoc.Root, "unreadable_count", 0);
			Log($"自建活动检查：{existingCount} 个店铺站点已有自建活动，{unknownCount} 个店铺站点接口未读取到自建活动，不代表后台没有，{unreadableCount} 个店铺站点无法确认。");
			foreach (string unreadableLine in SellerCampaignInfoLines(scopeDoc.Root, "unreadable", "无法确认是否已有自建活动，本次不允许创建。"))
			{
				Log(unreadableLine);
			}
			if (unknownCount <= 0)
			{
				return true;
			}
			List<SellerCampaignTarget> missingTargets = SellerCampaignTargets(scopeDoc.Root, "unknown_not_returned").ToList();
			foreach (string line in missingTargets.Select((SellerCampaignTarget target) => target.Label))
			{
				Log(line + "：接口未读取到自建活动，不代表后台没有；默认不创建，请先在网页后台核对。");
			}
			using SellerCampaignCreateDialog dialog = new SellerCampaignCreateDialog(missingTargets);
			if (dialog.ShowDialog(this) != DialogResult.OK)
			{
				Log("已取消自建活动创建引导，本次提交未继续执行。");
				return false;
			}
			List<SellerCampaignTarget> selectedTargets = dialog.SelectedTargets.ToList();
			var selectedTargetPayload = selectedTargets.Select((SellerCampaignTarget target) => new
			{
				accountId = target.AccountId,
				siteId = target.SiteId
			}).ToList();
			using ApiJson precheckDoc = await PostJsonAsync("/api/promotion-creation/seller-campaign/batch-precheck", new
			{
				accountIds = accountIds,
				filters = BuildFilters(),
				targetSelections = selectedTargetPayload,
				name = dialog.ActivityName,
				startDate = dialog.StartDate.ToString("yyyy-MM-dd'T'00:00:00", CultureInfo.InvariantCulture),
				finishDate = dialog.ApiFinishDate.ToString("yyyy-MM-dd'T'00:00:00", CultureInfo.InvariantCulture)
			});
			if (!Bool(precheckDoc.Root, "ok"))
			{
				throw new InvalidOperationException(StringValue(precheckDoc.Root, "error", "自建活动创建预检失败。"));
			}
			string[] errors = StringArray(precheckDoc.Root, "validation_errors").ToArray();
			if (errors.Length != 0)
			{
				string message = "自建活动创建预检未通过：" + Environment.NewLine + string.Join(Environment.NewLine, errors.Select((string error) => "- " + error));
				Log(message.Replace(Environment.NewLine, " "));
				MessageBox.Show(message, "自建活动创建预检", MessageBoxButtons.OK, MessageBoxIcon.Asterisk);
				return false;
			}
			if (DateTime.UtcNow.Year > 0)
			{
				int createReadyCount = IntValue(precheckDoc.Root, "preview_ready_count", 0);
				int precheckExistingCount = IntValue(precheckDoc.Root, "existing_count", 0);
				int precheckUnreadableCount = IntValue(precheckDoc.Root, "unreadable_count", 0);
				List<string> finalTargets = SellerCampaignTargetLines(precheckDoc.Root, "unknown_not_returned").ToList();
				foreach (string existingLine in SellerCampaignTargetLines(precheckDoc.Root, "existing"))
				{
					Log(existingLine + "：二次实时检查已读取到自建活动，本次禁止重复创建。");
				}
				foreach (string unreadableLine in SellerCampaignInfoLines(precheckDoc.Root, "unreadable", "二次实时检查无法确认，本次禁止创建。"))
				{
					Log(unreadableLine);
				}
				if (precheckUnreadableCount > 0)
				{
					MessageBox.Show("部分店铺站点二次检查无法确认是否已有自建活动，本次不创建。" + Environment.NewLine + string.Join(Environment.NewLine, SellerCampaignInfoLines(precheckDoc.Root, "unreadable", "无法确认，本次禁止创建。")), "创建自建活动", MessageBoxButtons.OK, MessageBoxIcon.Exclamation);
					return false;
				}
				if (createReadyCount <= 0)
				{
					Log("二次实时检查未发现需要创建的自建活动，活动列表将刷新后继续本次批量报名。");
					await RefreshActivitiesAsync(writeLog: false);
					return true;
				}
				Log($"自建活动创建预检完成：{createReadyCount} 个店铺站点已由人工确认可创建，{precheckExistingCount} 个店铺站点二次检查已有自建活动；只会创建 SELLER_CAMPAIGN。");
				foreach (string line5 in finalTargets)
				{
					Log(line5 + "：将创建自建活动 " + dialog.ActivityName + "。");
				}
				using StyledConfirmDialog finalConfirm = new StyledConfirmDialog("确认创建自建活动", $"将真实创建 {createReadyCount} 个 SELLER_CAMPAIGN 自建活动，活动名：{dialog.ActivityName}。{Environment.NewLine}{Environment.NewLine}请确认以下站点已在网页后台核对确实没有自建活动：{Environment.NewLine}{string.Join(Environment.NewLine, finalTargets)}{Environment.NewLine}{Environment.NewLine}创建完成并刷新活动后，会继续执行本次批量报名；不会创建官方活动。", "确认创建", "取消");
				if (finalConfirm.ShowDialog(this) != DialogResult.OK)
				{
					Log("已取消自建活动真实创建，本次提交未继续执行。");
					return false;
				}
				using ApiJson createDoc = await PostJsonAsync("/api/promotion-creation/seller-campaign/batch-create", new
				{
					accountIds = accountIds,
					filters = BuildFilters(),
					targetSelections = selectedTargetPayload,
					name = dialog.ActivityName,
					startDate = dialog.StartDate.ToString("yyyy-MM-dd'T'00:00:00", CultureInfo.InvariantCulture),
					finishDate = dialog.ApiFinishDate.ToString("yyyy-MM-dd'T'00:00:00", CultureInfo.InvariantCulture),
					confirmText = "CREATE_SELLER_CAMPAIGN"
				});
				int createdCount = IntValue(createDoc.Root, "created_count", 0);
				int failedCount = IntValue(createDoc.Root, "failed_count", 0);
				int recheckMissingCount = IntValue(createDoc.Root, "recheck_missing_count", 0);
				int notSelectedCount = IntValue(createDoc.Root, "not_selected_count", 0);
				int createUnreadableCount = IntValue(createDoc.Root, "unreadable_count", 0);
				int selectedCount = selectedTargets.Count;
				List<string> createFailedLines = SellerCampaignFailedLines(createDoc.Root, "failed").ToList();
				List<string> recheckMissingLines = SellerCampaignInfoLines(createDoc.Root, "recheck_missing", "自建活动接口成功，但刷新后未在该店铺站点找到；请稍后刷新活动列表核对。").ToList();
				List<string> notSelectedLines = SellerCampaignInfoLines(createDoc.Root, "not_selected", "未选择，本次不创建，不会计入成功或失败。").ToList();
				List<string> createUnreadableLines = SellerCampaignInfoLines(createDoc.Root, "unreadable", "无法确认是否已有自建活动，本次未创建。").ToList();
				Log($"自建活动创建汇总：选中 {selectedCount} 个，成功 {createdCount} 个，失败 {failedCount} 个，接口成功但回查未发现 {recheckMissingCount} 个，无法确认 {createUnreadableCount} 个，未选择 {notSelectedCount} 个。");
				foreach (string line3 in SellerCampaignCreatedLines(createDoc.Root, "created"))
				{
					Log(line3);
				}
				foreach (string line6 in recheckMissingLines)
				{
					Log(line6);
				}
				foreach (string line2 in createFailedLines)
				{
					Log(line2);
				}
				foreach (string line8 in createUnreadableLines)
				{
					Log(line8);
				}
				foreach (string line7 in notSelectedLines)
				{
					Log(line7);
				}
				await RefreshActivitiesAsync(writeLog: false);
				string continueMessage = "活动列表已刷新；将继续执行本次批量报名。";
				string failureMessage = createFailedLines.Count > 0
					? Environment.NewLine + Environment.NewLine + "创建失败：" + Environment.NewLine + string.Join(Environment.NewLine, createFailedLines)
					: "";
				string recheckMessage = recheckMissingLines.Count > 0
					? Environment.NewLine + Environment.NewLine + "接口成功但回查未发现：" + Environment.NewLine + string.Join(Environment.NewLine, recheckMissingLines)
					: "";
				string notSelectedMessage = notSelectedLines.Count > 0
					? Environment.NewLine + Environment.NewLine + "本次未选择：" + Environment.NewLine + string.Join(Environment.NewLine, notSelectedLines)
					: "";
				string unreadableMessage = createUnreadableLines.Count > 0
					? Environment.NewLine + Environment.NewLine + "无法确认，未创建：" + Environment.NewLine + string.Join(Environment.NewLine, createUnreadableLines)
					: "";
				MessageBox.Show($"自建活动创建完成：选中 {selectedCount} 个，成功 {createdCount} 个，失败 {failedCount} 个，回查未发现 {recheckMissingCount} 个，无法确认 {createUnreadableCount} 个，未选择 {notSelectedCount} 个。{Environment.NewLine}{continueMessage}{failureMessage}{recheckMessage}{unreadableMessage}{notSelectedMessage}", "创建自建活动", MessageBoxButtons.OK, (failedCount > 0 || recheckMissingCount > 0 || createUnreadableCount > 0) ? MessageBoxIcon.Exclamation : MessageBoxIcon.Asterisk);
				return true;
			}
			int ready = IntValue(precheckDoc.Root, "preview_ready_count", 0);
			Log($"自建活动创建预检完成：{ready} 个店铺站点可创建；本轮未执行 Mercado 真实创建。");
			foreach (string line4 in SellerCampaignTargetLines(precheckDoc.Root, "unknown_not_returned"))
			{
				Log(line4 + "：接口未读取到自建活动，已生成创建预检，等待真实创建确认。");
			}
			MessageBox.Show($"已生成 {ready} 个自建活动创建预检。" + Environment.NewLine + "请确认创建后继续本次批量报名。", "自建活动创建预检", MessageBoxButtons.OK, MessageBoxIcon.Asterisk);
			await RefreshActivitiesAsync(writeLog: false);
			return false;
		}

		private static IEnumerable<string> SellerCampaignTargetLines(JsonElement root, string propertyName)
		{
			return SellerCampaignTargets(root, propertyName).Select((SellerCampaignTarget target) => target.Label);
		}

		private string BuildExecutionConfirmationText(string action)
		{
			string store = SelectedComboText(_accountSelect);
			string site = SelectedComboText(_siteSelect);
			string actionText = LegacyActionText(action);
			if (string.Equals(action, "cancel", StringComparison.OrdinalIgnoreCase))
			{
				return $"店铺范围：{store}{Environment.NewLine}站点范围：{site}{Environment.NewLine}执行动作：{actionText}{Environment.NewLine}本次取消不使用折扣。请确认后执行。";
			}
			return $"店铺范围：{store}{Environment.NewLine}站点范围：{site}{Environment.NewLine}执行动作：{actionText}{Environment.NewLine}自建折扣：{_sellerDiscount.Value:0}%    官方折扣：{_officialDiscount.Value:0}%{Environment.NewLine}以上为最终执行参数，请确认后执行。";
		}

		private static IEnumerable<SellerCampaignTarget> SellerCampaignTargets(JsonElement root, string propertyName)
		{
			if (!root.TryGetProperty(propertyName, out var targets) || targets.ValueKind != JsonValueKind.Array)
			{
				yield break;
			}
			foreach (JsonElement target in targets.EnumerateArray())
			{
				string storeName = StringValue(target, "store_name", "当前店铺");
				string siteName = StringValue(target, "site_name", "");
				if (siteName.Length == 0)
				{
					siteName = SiteDisplayName(StringValue(target, "site_id", ""));
				}
				yield return new SellerCampaignTarget(StringValue(target, "account_id", ""), StringValue(target, "site_id", ""), storeName, siteName);
			}
		}

		private static IEnumerable<string> SellerCampaignCreatedLines(JsonElement root, string propertyName)
		{
			if (!root.TryGetProperty(propertyName, out var targets) || targets.ValueKind != JsonValueKind.Array)
			{
				yield break;
			}
			foreach (JsonElement target in targets.EnumerateArray())
			{
				string storeName = StringValue(target, "store_name", "当前店铺");
				string siteName = StringValue(target, "site_name", "");
				if (siteName.Length == 0)
				{
					siteName = SiteDisplayName(StringValue(target, "site_id", ""));
				}
				string promotionId = StringValue(target, "promotion_id", "");
				yield return $"{storeName} / {siteName}：自建活动已创建，活动ID {promotionId}。";
			}
		}

		private static IEnumerable<string> SellerCampaignFailedLines(JsonElement root, string propertyName)
		{
			if (!root.TryGetProperty(propertyName, out var targets) || targets.ValueKind != JsonValueKind.Array)
			{
				yield break;
			}
			foreach (JsonElement target in targets.EnumerateArray())
			{
				string storeName = StringValue(target, "store_name", "当前店铺");
				string siteName = StringValue(target, "site_name", "");
				if (siteName.Length == 0)
				{
					siteName = SiteDisplayName(StringValue(target, "site_id", ""));
				}
				string error = StringValue(target, "error", "创建失败");
				yield return $"{storeName} / {siteName}：自建活动创建失败，{error}。";
			}
		}

		private static IEnumerable<string> SellerCampaignInfoLines(JsonElement root, string propertyName, string defaultMessage)
		{
			if (!root.TryGetProperty(propertyName, out var targets) || targets.ValueKind != JsonValueKind.Array)
			{
				yield break;
			}
			foreach (JsonElement target in targets.EnumerateArray())
			{
				string storeName = StringValue(target, "store_name", "当前店铺");
				string siteName = StringValue(target, "site_name", "");
				if (siteName.Length == 0)
				{
					siteName = SiteDisplayName(StringValue(target, "site_id", ""));
				}
				string message = StringValue(target, "error", defaultMessage);
				yield return $"{storeName} / {siteName}：{message}";
			}
		}

		private async Task<ExecutionOutcome> StartAndPollExecutionJobAsync(string accountId, string action, int writeConcurrency, int siteConcurrency, int activityConcurrency, int readConcurrency)
		{
			string storeName = StoreNameForAccountId(accountId);
			string siteName = SelectedComboText(_siteSelect);
			using ApiJson doc = await PostJsonAsync("/api/execution/jobs/start", new
			{
				accountId = accountId,
				action = action,
				mode = "real",
				confirmText = "REAL_SUBMIT",
				filters = BuildFilters(),
				storeName = storeName,
				selectedStoreName = SelectedComboText(_accountSelect),
				selectedSiteName = siteName,
				priceMode = "discount",
				sellerDiscountPercent = _sellerDiscount.Value,
				officialDiscountPercent = _officialDiscount.Value,
				readConcurrency = Math.Max(1, Math.Min(readConcurrency, 20)),
				siteConcurrency = siteConcurrency,
				activityConcurrency = activityConcurrency,
				writeConcurrency = writeConcurrency
			});
			if (!doc.Root.TryGetProperty("job", out var job))
			{
				RenderExecutionResult(doc.StatusCode, doc.Root);
				return ExecutionOutcome.Empty(storeName, siteName, action);
			}
			string jobId = StringValue(job, "id", "");
			if (jobId.Length == 0)
			{
				throw new InvalidOperationException("后台执行任务创建失败。");
			}
			_currentExecutionJobId = jobId;
			lock (_currentExecutionJobIds)
			{
				_currentExecutionJobIds.Add(jobId);
			}
			try
			{
				return await PollExecutionJobAsync(jobId, storeName, siteName, action);
			}
			finally
			{
				lock (_currentExecutionJobIds)
				{
					_currentExecutionJobIds.Remove(jobId);
				}
			}
		}

		private async Task<ResolvedSubmitDecision> ResolveGlobalSubmitActionAsync(IReadOnlyList<string> accountIds, string selectedAction, bool writeConflictLogs = true)
		{
			if (!string.IsNullOrWhiteSpace(selectedAction))
			{
				return new ResolvedSubmitDecision(selectedAction);
			}
			List<StoreDecision> decisions = new List<StoreDecision>();
			foreach (string accountId in accountIds)
			{
				using ApiJson doc = await PostJsonAsync("/api/today/decision", new
				{
					accountId = accountId,
					filters = BuildFilters()
				});
				if (doc.Root.TryGetProperty("decision", out var decision2))
				{
					string action2 = StringValue(decision2, "today_action", StringValue(decision2, "action", ""));
					if (string.Equals(action2, "completed", StringComparison.OrdinalIgnoreCase))
					{
						action2 = StringValue(decision2, "action", action2);
					}
					string reason = StringValue(decision2, "reason", "");
					decisions.Add(new StoreDecision(StoreNameForAccountId(accountId), SelectedComboText(_siteSelect), action2, reason));
				}
			}
			string[] activeActions = decisions.Select((StoreDecision decision) => decision.Action).Where(delegate(string action)
			{
				switch (action)
				{
				case "enroll":
				case "update":
				case "cancel":
					return true;
				default:
					return false;
				}
			}).Distinct<string>(StringComparer.OrdinalIgnoreCase)
				.ToArray();
			if (activeActions.Length == 1)
			{
				return new ResolvedSubmitDecision(activeActions[0]);
			}
			if (activeActions.Length == 0)
			{
				throw new InvalidOperationException("自动判断没有得到可执行动作，请手动选择批量报活动、批量更新或批量取消。");
			}
			if (writeConflictLogs)
			{
				foreach (StoreDecision decision3 in decisions)
				{
					Log($"{decision3.StoreName} / {decision3.SiteName}：当前建议为{LegacyActionText(decision3.Action)}。{decision3.Reason}");
				}
			}
			throw new InvalidOperationException("不同店铺需要不同动作，本次自动判断已停止；请手动选择批量报活动、批量更新或批量取消，或分开执行。");
		}

		private decimal ClampDiscountValue(decimal value)
		{
			if (value < _sellerDiscount.Minimum)
			{
				return _sellerDiscount.Minimum;
			}
			if (value > _sellerDiscount.Maximum)
			{
				return _sellerDiscount.Maximum;
			}
			return value;
		}

		private async Task<(int Total, int Active, int Inactive)> CountSelectedStoreSitesAsync(IReadOnlyList<string> accountIds)
		{
			string selectedSiteId = SelectedValue(_siteSelect);
			Dictionary<string, bool> sitesByKey = new Dictionary<string, bool>(StringComparer.OrdinalIgnoreCase);
			foreach (string accountId in accountIds)
			{
				using JsonDocument sitesDoc = await GetJsonAsync("/api/accounts/" + Uri.EscapeDataString(accountId) + "/sites");
				if (!sitesDoc.RootElement.TryGetProperty("sites", out var sites) || sites.ValueKind != JsonValueKind.Array)
				{
					continue;
				}
				foreach (JsonElement site in sites.EnumerateArray())
				{
					string siteId = StringValue(site, "site_id", "");
					if ((selectedSiteId.Length <= 0 || string.Equals(siteId, selectedSiteId, StringComparison.OrdinalIgnoreCase)) && siteId.Length != 0)
					{
						string key = accountId + "|" + siteId;
						bool hasActivity = IntValue(site, "total", 0) > 0 || IntValue(site, "last_promotion_count", 0) > 0;
						sitesByKey[key] = (sitesByKey.TryGetValue(key, out var current) ? (current || hasActivity) : hasActivity);
					}
				}
			}
			int total = sitesByKey.Count;
			int active = sitesByKey.Values.Count((bool value) => value);
			return (Total: total, Active: active, Inactive: Math.Max(0, total - active));
		}

		private async Task<(int Concurrency, string Source)> ResolveReadConcurrencyPlanAsync()
		{
			try
			{
				using JsonDocument doc = await GetJsonAsync("/api/concurrency-benchmark/results");
				if (doc.RootElement.TryGetProperty("results", out var results) && results.TryGetProperty("read", out var read) && read.ValueKind == JsonValueKind.Object)
				{
					int suggested = Math.Max(1, Math.Min(IntValue(read, "suggested_read_concurrency", (int)_readConcurrency), 20));
					string finished = ShortDate(StringValue(read, "finished_at", ""));
					string source = ((finished.Length > 0) ? $"来自 {finished} 只读压测，建议 {suggested}" : $"来自上次只读压测，建议 {suggested}");
					return (Concurrency: suggested, Source: source);
				}
			}
			catch
			{
			}
			int fallback = Math.Max(1, Math.Min((int)_readConcurrency, 20));
			return (Concurrency: fallback, Source: $"未实测，使用本地保守值 {fallback}");
		}

		private async Task<(int Concurrency, string Source)> ResolveWriteConcurrencyPlanAsync()
		{
			try
			{
				using JsonDocument doc = await GetJsonAsync("/api/concurrency-benchmark/results");
				if (doc.RootElement.TryGetProperty("results", out var results) && results.TryGetProperty("write", out var write) && write.ValueKind == JsonValueKind.Object)
				{
					int stable = IntValue(write, "suggested_write_concurrency", 0);
					if (stable >= 350)
					{
						stable = Math.Max(1, Math.Min(stable, 10000));
						int suggested = StableToDailyWriteConcurrency(stable);
						string finished = ShortDate(StringValue(write, "finished_at", ""));
						string source = ((finished.Length > 0) ? $"来自 {finished} 真实压测，已验证稳定 {stable}，日常建议 {suggested}" : $"来自上次真实压测，已验证稳定 {stable}，日常建议 {suggested}");
						return (Concurrency: suggested, Source: source);
					}
				}
				if (doc.RootElement.TryGetProperty("results", out var latestResults) && latestResults.TryGetProperty("write_latest_status", out var latest) && latest.ValueKind == JsonValueKind.Object)
				{
					int stable2 = Math.Max(1, Math.Min(IntValue(latest, "verified_stable_concurrency", 350), 10000));
					int suggested2 = Math.Max(1, Math.Min(IntValue(latest, "daily_recommended_max", 320), 10000));
					return (Concurrency: suggested2, Source: $"真实测试线程最新回传：已验证稳定 {stable2}，日常建议 {300}-{suggested2}");
				}
			}
			catch
			{
			}
			return (Concurrency: 2, Source: $"未实测，使用保守值 {2}");
		}

		private static int StableToDailyWriteConcurrency(int stable)
		{
			stable = Math.Max(1, Math.Min(stable, 10000));
			if (stable <= 2)
			{
				return stable;
			}
			return Math.Max(1, Math.Min(10000, (int)Math.Floor((decimal)stable * 0.8m)));
		}

		private static bool IsExecutionJobNotFoundMessage(string message)
		{
			if (!message.Contains("\"ok\":false", StringComparison.OrdinalIgnoreCase) || !message.Contains("\"error\"", StringComparison.OrdinalIgnoreCase))
			{
				if (message.Contains("execution job", StringComparison.OrdinalIgnoreCase))
				{
					return message.Contains("not found", StringComparison.OrdinalIgnoreCase);
				}
				return false;
			}
			return true;
		}

		private async Task<JsonDocument> GetExecutionJobJsonAsync(string jobId, string storeName, string siteName, string requestedAction)
		{
			try
			{
				return await GetJsonAsync("/api/execution/jobs/" + Uri.EscapeDataString(jobId));
			}
			catch (InvalidOperationException ex) when (IsExecutionJobNotFoundMessage(ex.Message))
			{
				Log(storeName + " / " + siteName + "：程序组件已重新准备或任务状态已过期，已停止继续查询；已完成部分请查看历史记录。");
				string safeJobId = jobId.Replace("\\", "\\\\", StringComparison.Ordinal).Replace("\"", "\\\"", StringComparison.Ordinal);
				return JsonDocument.Parse("{\"ok\":true,\"job\":{\"id\":\"" + safeJobId + "\",\"status\":\"cancelled\",\"error\":\"expired\"}}");
			}
		}

		private async Task<ExecutionOutcome> PollExecutionJobAsync(string jobId, string storeName, string siteName, string requestedAction)
		{
			int logIndex = 0;
			while (true)
			{
				using JsonDocument doc = await GetExecutionJobJsonAsync(jobId, storeName, siteName, requestedAction);
				if (!doc.RootElement.TryGetProperty("job", out var job))
				{
					throw new InvalidOperationException("后台执行任务状态读取失败。");
				}
				JsonElement userLogs;
				JsonElement logProperty = ((job.TryGetProperty("userLogs", out userLogs) && userLogs.ValueKind == JsonValueKind.Array) ? userLogs : default(JsonElement));
				if (logProperty.ValueKind != JsonValueKind.Array && job.TryGetProperty("logs", out var fallbackLogs) && fallbackLogs.ValueKind == JsonValueKind.Array)
				{
					logProperty = fallbackLogs;
				}
				if (logProperty.ValueKind == JsonValueKind.Array)
				{
					int index = 0;
					foreach (JsonElement entry in logProperty.EnumerateArray())
					{
						if (index >= logIndex)
						{
							string message = StringValue(entry, "message", "");
							if (message.Length > 0)
							{
								Log(message);
							}
						}
						index++;
					}
					logIndex = index;
				}
				string status = StringValue(job, "status", "");
				if (string.Equals(status, "completed", StringComparison.OrdinalIgnoreCase) || string.Equals(status, "failed", StringComparison.OrdinalIgnoreCase) || string.Equals(status, "cancelled", StringComparison.OrdinalIgnoreCase))
				{
					if (job.TryGetProperty("result", out var result) && result.ValueKind == JsonValueKind.Object)
					{
						return ExecutionOutcome.FromResult(storeName, siteName, requestedAction, result);
					}
					Log(TerminalJobMessage(status, StringValue(job, "error", "")));
					return ExecutionOutcome.Empty(storeName, siteName, requestedAction);
				}
				await Task.Delay(1500);
			}
		}

		private static string StoreActionSummaryText(ExecutionOutcome outcome)
		{
			string actionText = LegacyActionText(outcome.Action);
			string sellerText = $"自建 {outcome.SellerSuccess}/{outcome.SellerProcessed}";
			string officialText = $"官方 {outcome.OfficialSuccess}/{outcome.OfficialProcessed}";
			List<string> specialParts = new List<string>();
			if (outcome.SmartSkipped > 0)
			{
				specialParts.Add($"SMART 跳过 {outcome.SmartSkipped}");
			}
			if (outcome.LightningSkipped > 0)
			{
				specialParts.Add($"LIGHTNING 跳过 {outcome.LightningSkipped}");
			}
			string specialText = ((specialParts.Count > 0) ? ("，" + string.Join("，", specialParts)) : "");
			string sellerReason = ((outcome.SellerProcessed == 0) ? "；自建原因：未匹配到 SELLER_CAMPAIGN 活动或无可处理商品" : "");
			return $"{outcome.StoreName} / {outcome.SiteName}：{actionText}完成，活动 {outcome.PromotionsTotal} 个，商品 {outcome.DisplayTotal}，成功 {outcome.Success}，失败 {outcome.Failed}，跳过 {outcome.Skipped}；{sellerText}，{officialText}{specialText}{sellerReason}。";
		}

		private static string OverallActionSummaryText(string action, IReadOnlyList<ExecutionOutcome> outcomes)
		{
			int total = outcomes.Sum((ExecutionOutcome item) => item.DisplayTotal);
			int success = outcomes.Sum((ExecutionOutcome item) => item.Success);
			int failed = outcomes.Sum((ExecutionOutcome item) => item.Failed);
			int skipped = outcomes.Sum((ExecutionOutcome item) => item.Skipped);
			int sellerSuccess = outcomes.Sum((ExecutionOutcome item) => item.SellerSuccess);
			int officialSuccess = outcomes.Sum((ExecutionOutcome item) => item.OfficialSuccess);
			return $"本次{LegacyActionText(action)}总汇总：店铺站点 {outcomes.Count} 个，商品 {total}，成功 {success}，失败 {failed}，跳过 {skipped}；自建成功 {sellerSuccess}，官方成功 {officialSuccess}。";
		}

		private async Task CancelCurrentExecutionJobAsync()
		{
			if (!_executionJobRunning)
			{
				return;
			}
			List<string> jobIds;
			lock (_currentExecutionJobIds)
			{
				jobIds = _currentExecutionJobIds.ToList();
			}
			if (jobIds.Count == 0 && _currentExecutionJobId.Length > 0)
			{
				jobIds.Add(_currentExecutionJobId);
			}
			if (jobIds.Count == 0)
			{
				return;
			}
			try
			{
				foreach (string jobId in jobIds.Distinct<string>(StringComparer.OrdinalIgnoreCase))
				{
					using (await PostJsonAsync("/api/execution/jobs/" + Uri.EscapeDataString(jobId) + "/cancel", new { }))
					{
					}
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
			using JsonDocument doc = await GetJsonAsync("/api/tasks?limit=300");
			_taskGrid.Rows.Clear();
			if (!doc.RootElement.TryGetProperty("tasks", out var tasks) || tasks.ValueKind != JsonValueKind.Array)
			{
				ShowEmptyTasksRow("未读取到批次记录。");
				return;
			}
			List<TaskGridRow> rows = new List<TaskGridRow>();
			foreach (JsonElement task in tasks.EnumerateArray())
			{
				AddOrMergeTaskGridRow(rows, BuildTaskGridRow(task));
			}
			foreach (TaskGridRow row in rows)
			{
				int index = _taskGrid.Rows.Add(row.TimeText, row.ActionText, row.SellerActivity, row.OfficialActivity, row.ModeText, row.QuantityText, row.Total, row.Success, row.Failed, row.ReasonText);
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
			int taskId = IntValue(task, "id", 0);
			List<int> taskIds = IntArray(task, "task_ids").ToList();
			if (taskIds.Count == 0 && taskId > 0)
			{
				taskIds.Add(taskId);
			}
			string accountId = StringValue(task, "account_id", "");
			string siteId = StringValue(task, "site_id", "");
			string promotionId = StringValue(task, "promotion_id", "");
			string promotionType = StringValue(task, "promotion_type", "");
			bool isBatch = string.Equals(promotionId, "__BATCH__", StringComparison.OrdinalIgnoreCase) || string.Equals(promotionType, "BATCH", StringComparison.OrdinalIgnoreCase);
			bool isSeller = string.Equals(promotionType, "SELLER_CAMPAIGN", StringComparison.OrdinalIgnoreCase) || promotionId.StartsWith("C-", StringComparison.OrdinalIgnoreCase);
			string mode = ModeDisplayName(StringValue(task, "mode", ""));
			string actionRaw = StringValue(task, "action", "");
			int total = IntValue(task, "total_count", 0);
			int success = ((mode == "预览") ? IntValue(task, "planned_count", 0) : IntValue(task, "success_count", 0));
			int failed = ((mode == "预览") ? IntValue(task, "skipped_count", 0) : IntValue(task, "failed_count", 0));
			int activityTotal = ((!isBatch) ? 1 : IntValue(task, "promotions_total", 0));
			string activityName = (isBatch ? BatchActivityDisplayName(task) : TaskActivityDisplayName(task));
			bool isCancelAction = string.Equals(actionRaw, "cancel", StringComparison.OrdinalIgnoreCase);
			string sellerActivity = isCancelAction ? "" : (isBatch ? StringValue(task, "seller_activity_text", "") : (isSeller ? activityName : ""));
			string officialActivity = isCancelAction ? "" : (isBatch ? StringValue(task, "official_activity_text", "") : (isSeller ? "" : activityName));
			if (isBatch && sellerActivity.Length == 0 && officialActivity.Length == 0)
			{
				sellerActivity = activityName;
				officialActivity = activityName;
			}
			string createdText = StringValue(task, "created_at", "");
			DateTime.TryParse(createdText, out var createdAt);
			string storeText = StringValue(task, "store_name", "");
			if (storeText.Length == 0)
			{
				storeText = StoreNameForAccountId(accountId);
			}
			string siteText = StringValue(task, "site_name", "");
			if (siteText.Length == 0)
			{
				siteText = (isBatch ? "多个站点" : SiteDisplayName(siteId));
			}
			JsonElement inlineDetails;
			List<string> detailLines = ((task.TryGetProperty("details", out inlineDetails) && inlineDetails.ValueKind == JsonValueKind.Array) ? TaskDetailLines(task, storeText, siteText, activityName, promotionId, promotionType, total, success, failed, IntValue(task, "skipped_count", 0), TaskReason(task)).ToList() : new List<string>());
			List<string> failureReasonDetails = TaskFailureReasonDetails(task).ToList();
			List<string> skippedReasonDetails = TaskSkippedReasonDetails(task).ToList();
			return new TaskGridRow(taskIds, createdAt, ShortDate(createdText), LegacyActionText(actionRaw), storeText, siteText, sellerActivity, officialActivity, mode, QuantityText(actionRaw), total, success, failed, IntValue(task, "skipped_count", 0), StringValue(task, "short_failure_reason", TaskReason(task)), TaskMergeKey(createdAt, actionRaw, StringValue(task, "mode", ""), isSeller ? "seller" : (isBatch ? "batch" : "official"), activityName, isBatch), isBatch, activityTotal, StringValue(task, "summary_json", ""), detailLines, failureReasonDetails, skippedReasonDetails);
		}

		private static IEnumerable<string> TaskDetailLines(JsonElement task, string storeText, string siteText, string activityName, string promotionId, string promotionType, int total, int success, int failed, int skipped, string reason)
		{
			if (task.TryGetProperty("details", out var details) && details.ValueKind == JsonValueKind.Array)
			{
				foreach (JsonElement detail in details.EnumerateArray())
				{
					string detailStore = StringValue(detail, "store_name", storeText);
					string detailSite = StringValue(detail, "site_name", "");
					if (detailSite.Length == 0)
					{
						detailSite = SiteDisplayName(StringValue(detail, "site_id", ""));
					}
					string detailName = StringValue(detail, "promotion_name", "");
					if (detailName.Length == 0)
					{
						detailName = TaskActivityDisplayName(detail);
					}
					yield return BuildTaskDetailLine(IntValue(detail, "id", 0), detailStore, detailSite, detailName, StringValue(detail, "promotion_id", ""), StringValue(detail, "promotion_type", ""), IntValue(detail, "total_count", 0), IntValue(detail, "success_count", 0), IntValue(detail, "failed_count", 0), IntValue(detail, "skipped_count", 0), TaskReason(detail));
				}
			}
			else
			{
				JsonElement id;
				int parsed;
				yield return BuildTaskDetailLine((task.TryGetProperty("id", out id) && id.TryGetInt32(out parsed)) ? parsed : 0, storeText, siteText, activityName, promotionId, promotionType, total, success, failed, skipped, reason);
			}
		}

		private static string BuildTaskDetailLine(int taskId, string storeText, string siteText, string activityName, string promotionId, string promotionType, int total, int success, int failed, int skipped, string reason)
		{
			string activity = (string.IsNullOrWhiteSpace(activityName) ? "未命名活动" : activityName);
			string idText = ((string.IsNullOrWhiteSpace(promotionId) || promotionId == "__BATCH__") ? "" : $"（{promotionId} / {PromotionTypeDisplayName(promotionType)}）");
			return $"{storeText} / {siteText} / {activity}{idText}：商品 {total}，成功 {success}，失败 {failed}，跳过 {skipped}。{reason}";
		}

		private static void AddOrMergeTaskGridRow(List<TaskGridRow> rows, TaskGridRow next)
		{
			TaskGridRow next2 = next;
			TaskGridRow? existing = rows.FirstOrDefault((TaskGridRow row) => row.MergeKey == next2.MergeKey);
			if (existing == null || next2.MergeKey.Length == 0)
			{
				rows.Add(next2);
				return;
			}
			existing.TaskIds.AddRange(next2.TaskIds);
			existing.StoreNames.UnionWith(next2.StoreNames);
			existing.SiteNames.UnionWith(next2.SiteNames);
			existing.StoreText = ScopeText(existing.StoreNames, "多个店铺");
			existing.SiteText = ScopeText(existing.SiteNames, "多个站点");
			existing.Total += next2.Total;
			existing.Success += next2.Success;
			existing.Failed += next2.Failed;
			existing.Skipped += next2.Skipped;
			existing.SellerActivity = MergeActivityText(existing.SellerActivity, next2.SellerActivity);
			existing.OfficialActivity = MergeActivityText(existing.OfficialActivity, next2.OfficialActivity);
			existing.ReasonText = MergeReasonText(existing.ReasonText, next2.ReasonText);
			existing.DetailLines.AddRange(next2.DetailLines);
			existing.FailureReasonDetails.AddRange(next2.FailureReasonDetails);
			existing.SkippedReasonDetails.AddRange(next2.SkippedReasonDetails);
			existing.ActivityTotal += next2.ActivityTotal;
			existing.IsBatch = existing.IsBatch || next2.IsBatch;
			if (existing.IsBatch)
			{
				existing.ReasonText = AppendScopeHint(existing.ReasonText, existing.StoreNames, existing.SiteNames);
				return;
			}
			int activityCount = existing.TaskIds.Distinct().Count();
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
			if (isBatch)
			{
				return "";
			}
			if (!isBatch && (activityName.Length == 0 || activityName.StartsWith("多个活动", StringComparison.Ordinal)))
			{
				return "";
			}
			string dateKey = ((createdAt == default(DateTime)) ? "" : createdAt.ToString("yyyy-MM-dd"));
			string activityKey = (isBatch ? "__batch__" : activityName.Trim().ToLowerInvariant());
			return $"{dateKey}|{action}|{mode}|{category}|{activityKey}";
		}

		private static string ActivityCountText(string name, int count)
		{
			string clean = Regex.Replace(name, "（\\d+个活动）$", "");
			if (count <= 1)
			{
				return clean;
			}
			return $"{clean}（{count}个活动）";
		}

		private static string MergeActivityText(string first, string second)
		{
			string[] values = (from text in new string[2] { first, second }.Where((string text) => !string.IsNullOrWhiteSpace(text)).SelectMany((string text) => text.Split(new char[3] { '、', ',', '，' }, StringSplitOptions.RemoveEmptyEntries))
				select text.Trim() into text
				where text.Length > 0
				select text).Distinct<string>(StringComparer.OrdinalIgnoreCase).OrderBy<string, string>((string text) => text, StringComparer.OrdinalIgnoreCase).ToArray();
			if (values.Length != 0)
			{
				return string.Join("、", values);
			}
			return "";
		}

		private static string ScopeText(IReadOnlyCollection<string> names, string multiLabel)
		{
			string[] clean = (from name in names.Where((string name) => !string.IsNullOrWhiteSpace(name)).Distinct<string>(StringComparer.OrdinalIgnoreCase)
				orderby name
				select name).ToArray();
			if (clean.Length == 0)
			{
				return "";
			}
			if (clean.Length == 1)
			{
				return clean[0];
			}
			return $"{multiLabel}（{clean.Length}个）";
		}

		private static string MergeReasonText(string first, string second)
		{
			if (string.IsNullOrWhiteSpace(first))
			{
				return second;
			}
			if (string.IsNullOrWhiteSpace(second) || first.Contains(second, StringComparison.Ordinal))
			{
				return first;
			}
			return first + "；" + second;
		}

		private static string AppendScopeHint(string reason, IReadOnlyCollection<string> stores, IReadOnlyCollection<string> sites)
		{
			bool num = stores.Where((string name) => !string.IsNullOrWhiteSpace(name)).Distinct<string>(StringComparer.OrdinalIgnoreCase).Count() > 1;
			bool multiSite = sites.Where((string name) => !string.IsNullOrWhiteSpace(name)).Distinct<string>(StringComparer.OrdinalIgnoreCase).Count() > 1;
			if (!num && !multiSite)
			{
				return reason;
			}
			if (reason.Contains("范围：多个店铺/站点，右键查看详情", StringComparison.Ordinal))
			{
				return reason;
			}
			if (!string.IsNullOrWhiteSpace(reason))
			{
				return reason + "；范围：多个店铺/站点，右键查看详情";
			}
			return "范围：多个店铺/站点，右键查看详情";
		}

		private void CopySelectedTaskRows()
		{
			List<DataGridViewRow> rows = SelectedTaskGridRows();
			if (rows.Count == 0)
			{
				Log("请先选择要复制的记录。");
				return;
			}
			List<string> lines = new List<string> { "时间\t动作\t自建活动\t官方活动\t类型\t数量类型\t商品数\t成功\t失败\t失败原因" };
			foreach (DataGridViewRow row in rows)
			{
				lines.Add(string.Join("\t", from DataGridViewCell cell in row.Cells
					select Convert.ToString(cell.Value)?.Replace("\t", " ") ?? ""));
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
			List<DataGridViewRow> rows = SelectedTaskGridRows();
			if (rows.Count == 0)
			{
				Log("请先选择要查看的记录。");
				return;
			}
			await EnsureSelectedTaskDetailsAsync(rows);
			string detail = BuildSelectedTaskDetails(rows);
			using TextDetailForm form = new TextDetailForm("记录详情", detail);
			form.ShowDialog(this);
		}

		private async Task CopySelectedTaskDetailsAsync()
		{
			List<DataGridViewRow> rows = SelectedTaskGridRows();
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
			foreach (DataGridViewRow row in rows)
			{
				object? tag = row.Tag;
				if (!(tag is TaskGridRow taskRow) || taskRow.DetailLines.Count > 0)
				{
					continue;
				}
				int[] ids = (from id in taskRow.TaskIds.Distinct()
					where id > 0
					select id).ToArray();
				if (ids.Length == 0)
				{
					continue;
				}
				using JsonDocument doc = await GetJsonAsync("/api/tasks/details?taskIds=" + Uri.EscapeDataString(string.Join(",", ids)));
				if (!doc.RootElement.TryGetProperty("details", out var details) || details.ValueKind != JsonValueKind.Array)
				{
					continue;
				}
				foreach (JsonElement detail in details.EnumerateArray())
				{
					string detailStore = StringValue(detail, "store_name", taskRow.StoreScopeText);
					string detailSite = StringValue(detail, "site_name", "");
					if (detailSite.Length == 0)
					{
						detailSite = SiteDisplayName(StringValue(detail, "site_id", ""));
					}
					string detailName = StringValue(detail, "promotion_name", "");
					if (detailName.Length == 0)
					{
						detailName = TaskActivityDisplayName(detail);
					}
					taskRow.DetailLines.Add(BuildTaskDetailLine(IntValue(detail, "id", 0), detailStore, detailSite, detailName, StringValue(detail, "promotion_id", ""), StringValue(detail, "promotion_type", ""), IntValue(detail, "total_count", 0), IntValue(detail, "success_count", 0), IntValue(detail, "failed_count", 0), IntValue(detail, "skipped_count", 0), TaskReason(detail)));
				}
				if (taskRow.DetailLines.Count == 0)
				{
					taskRow.DetailLines.Add("未读取到详情。");
				}
			}
		}

		private void ShowSelectedTaskSummaryInLog()
		{
			if (_taskGrid.SelectedRows.Count != 1 || !(_taskGrid.SelectedRows[0].Tag is TaskGridRow taskRow) || (taskRow.FailureReasonDetails.Count == 0 && taskRow.SkippedReasonDetails.Count == 0 && taskRow.Skipped == 0))
			{
				return;
			}
			StringBuilder builder = new StringBuilder();
			StringBuilder stringBuilder = builder;
			StringBuilder stringBuilder2 = stringBuilder;
			StringBuilder.AppendInterpolatedStringHandler handler = new StringBuilder.AppendInterpolatedStringHandler(8, 2, stringBuilder);
			handler.AppendFormatted(taskRow.TimeText);
			handler.AppendLiteral(" ");
			handler.AppendFormatted(taskRow.ActionText);
			handler.AppendLiteral("：完整原因");
			stringBuilder2.AppendLine(ref handler);
			if (taskRow.FailureReasonDetails.Count > 0)
			{
				builder.AppendLine("失败原因汇总：");
			}
			foreach (string reason in taskRow.FailureReasonDetails.Distinct())
			{
				builder.AppendLine("- " + reason);
			}
			if (taskRow.SkippedReasonDetails.Count > 0)
			{
				builder.AppendLine("未执行/跳过明细：");
			}
			foreach (string reason in taskRow.SkippedReasonDetails.Distinct())
			{
				builder.AppendLine("- " + reason);
			}
			if (taskRow.Skipped > 0 && taskRow.SkippedReasonDetails.Count == 0)
			{
				stringBuilder = builder;
				StringBuilder stringBuilder3 = stringBuilder;
				handler = new StringBuilder.AppendInterpolatedStringHandler(7, 1, stringBuilder);
				handler.AppendLiteral("- 未执行/跳过数量：");
				handler.AppendFormatted(taskRow.Skipped);
				stringBuilder3.AppendLine(ref handler);
			}
			string text = builder.ToString().Trim();
			if (!(text == _lastTaskSelectionDetails))
			{
				_lastTaskSelectionDetails = text;
				Log(text);
			}
		}

		private static string BuildSelectedTaskDetails(IEnumerable<DataGridViewRow> rows)
		{
			StringBuilder builder = new StringBuilder();
			foreach (DataGridViewRow row in rows)
			{
				if (!(row.Tag is TaskGridRow taskRow))
				{
					continue;
				}
				if (builder.Length > 0)
				{
					builder.AppendLine().AppendLine();
				}
				StringBuilder stringBuilder = builder;
				StringBuilder stringBuilder2 = stringBuilder;
				StringBuilder.AppendInterpolatedStringHandler handler = new StringBuilder.AppendInterpolatedStringHandler(14, 6, stringBuilder);
				handler.AppendFormatted(taskRow.TimeText);
				handler.AppendLiteral(" ");
				handler.AppendFormatted(taskRow.ActionText);
				handler.AppendLiteral(" ");
				handler.AppendFormatted(taskRow.ModeText);
				handler.AppendLiteral("：商品 ");
				handler.AppendFormatted(taskRow.Total);
				handler.AppendLiteral("，成功 ");
				handler.AppendFormatted(taskRow.Success);
				handler.AppendLiteral("，失败 ");
				handler.AppendFormatted(taskRow.Failed);
				stringBuilder2.AppendLine(ref handler);
				builder.AppendLine(TaskQuantityNote(taskRow));
				stringBuilder = builder;
				StringBuilder stringBuilder3 = stringBuilder;
				handler = new StringBuilder.AppendInterpolatedStringHandler(6, 2, stringBuilder);
				handler.AppendLiteral("范围：");
				handler.AppendFormatted(taskRow.StoreScopeText);
				handler.AppendLiteral(" / ");
				handler.AppendFormatted(taskRow.SiteScopeText);
				stringBuilder3.AppendLine(ref handler);
				if (taskRow.FailureReasonDetails.Count > 0)
				{
					builder.AppendLine("失败原因汇总：");
					foreach (string reason in taskRow.FailureReasonDetails)
					{
						builder.AppendLine("- " + reason);
					}
				}
				if (taskRow.SkippedReasonDetails.Count > 0)
				{
					builder.AppendLine("未执行/跳过明细：");
					foreach (string reason in taskRow.SkippedReasonDetails)
					{
						builder.AppendLine("- " + reason);
					}
				}
				builder.AppendLine("明细：");
				foreach (string line in taskRow.DetailLines.Distinct())
				{
					builder.AppendLine("- " + line);
				}
			}
			return builder.ToString().Trim();
		}

		private async Task DeleteSelectedTaskRowsAsync()
		{
			List<DataGridViewRow> rows = SelectedTaskGridRows();
			int[] ids = (from id in rows.SelectMany((DataGridViewRow row) => (!(row.Tag is TaskGridRow taskGridRow)) ? new List<int>() : taskGridRow.TaskIds).Distinct()
				where id > 0
				select id).ToArray();
			if (ids.Length == 0)
			{
				Log("请先选择要删除的本地记录。");
				return;
			}
			bool num = rows.Any((DataGridViewRow row) => string.Equals(Convert.ToString(row.Cells["reason"].Value), "执行中", StringComparison.OrdinalIgnoreCase) || (Convert.ToString(row.Cells["reason"].Value)?.Contains("执行中") ?? false));
			string message = $"将删除 {ids.Length} 条本地记录及其本地结果明细。\r\n\r\n不会删除 Mercado 活动、商品、授权或设置。";
			if (num)
			{
				message += "\r\n\r\n选中记录可能仍在运行；删除只影响本地显示记录，不会停止后台任务。";
			}
			if (MessageBox.Show(message, "删除本地记录", MessageBoxButtons.OKCancel, MessageBoxIcon.Exclamation) != DialogResult.OK)
			{
				return;
			}
			using ApiJson doc = await PostJsonAsync("/api/tasks/delete", new
			{
				taskIds = ids
			});
			if (!Bool(doc.Root, "ok"))
			{
				throw new InvalidOperationException(StringValue(doc.Root, "error", $"删除失败：HTTP {doc.StatusCode}"));
			}
			int deleted = IntValue(doc.Root, "deleted", 0);
			Log($"已删除本地记录 {deleted} 条。");
			await RefreshTasksAsync();
		}

		private List<DataGridViewRow> SelectedTaskGridRows()
		{
			return (from DataGridViewRow row in _taskGrid.SelectedRows
				where !row.IsNewRow && row.Tag is TaskGridRow
				orderby row.Index
				select row).ToList();
		}

		private async Task ShowSettingsAsync()
		{
			string benchmarkSummary = await LoadConcurrencyBenchmarkSummaryAsync();
			List<OperatingSiteOption> operatingSiteOptions = await LoadOperatingSiteOptionsAsync();
			SettingsDialog dialog = new SettingsDialog(_authDir, _outputDir, _sellerDiscount.Value, _officialDiscount.Value, _readConcurrency, _previewConcurrency, _writeConcurrency, StoreListText(), AuthorizedAccountsText(), _accounts, _storeAliases, _operatingSites, operatingSiteOptions, benchmarkSummary);
			try
			{
				dialog.AuthorizeRequestedAsync = async delegate
				{
					await StartOAuthAuthorizationFromConfigAsync(dialog);
				};
				dialog.CompleteAuthorizationRequestedAsync = async delegate
				{
					await CompleteOAuthAuthorizationFromCallbackAsync(dialog);
				};
				dialog.SaveStoreAliasRequestedAsync = async delegate
				{
					await SaveStoreAliasFromSettingsAsync(dialog);
				};
				dialog.RefreshAccountsRequestedAsync = async delegate
				{
					await RunUiTaskAsync("刷新账号/店铺", async delegate
					{
						await LoadAccountsAsync();
						await RefreshActivitiesAsync(writeLog: false);
						await RefreshTasksAsync();
						dialog.SetAccountSummary(StoreListText(), AuthorizedAccountsText());
						dialog.SetAuthorizationStatus("账号/店铺已刷新。");
					});
				};
				if (dialog.ShowDialog(this) != DialogResult.OK)
				{
					return;
				}
				await RunUiTaskAsync("保存设置", async delegate
				{
					using (await PostJsonAsync("/api/settings", new
					{
						authDir = dialog.AuthDir,
						outputDir = dialog.OutputDir,
						sellerDefaultDiscount = dialog.SellerDiscount,
						officialDefaultDiscount = dialog.OfficialDiscount,
						readConcurrency = dialog.ReadConcurrency,
						previewConcurrency = dialog.PreviewConcurrency,
						writeConcurrency = dialog.WriteConcurrency,
						storeAliases = _storeAliases,
						operatingSites = dialog.OperatingSites,
						defaultFilters = BuildFilters()
					}))
					{
						await LoadSettingsAsync();
						await RefreshActivitiesAsync(writeLog: false);
						Log("设置已保存。");
					}
				});
			}
			finally
			{
				if (dialog != null)
				{
					((IDisposable)dialog).Dispose();
				}
			}
		}

		private async Task<string> LoadConcurrencyBenchmarkSummaryAsync()
		{
			try
			{
				using JsonDocument doc = await GetJsonAsync("/api/concurrency-benchmark/results");
				if (!doc.RootElement.TryGetProperty("results", out var results))
				{
					return $"并发实测：后台压测工具已启用逐商品落盘；当前设置={_writeConcurrency:0}；真实测试线程最新回传：350 两次稳定，日常建议 {300}-{320}。";
				}
				string writeText = $"后台压测工具已启用逐商品落盘；当前设置={_writeConcurrency:0}；真实测试线程最新回传：350 两次稳定，日常建议 {300}-{320}，追求速度可手动设 350。";
				JsonElement write;
				if (results.TryGetProperty("write_latest_status", out var latest) && latest.ValueKind == JsonValueKind.Object)
				{
					int stable = IntValue(latest, "verified_stable_concurrency", 350);
					int min = IntValue(latest, "daily_recommended_min", 300);
					int max = IntValue(latest, "daily_recommended_max", 320);
					string note = StringValue(latest, "verified_note", "350 两次稳定");
					writeText = $"后台压测工具已启用逐商品落盘；当前设置={_writeConcurrency:0}；当前重复验证最高稳定档：{stable}（真实测试线程，10% update，{note}）；建议日常写入并发：保守 {min}-{max}，追求速度可手动设 350。";
				}
				else if (results.TryGetProperty("write", out write) && write.ValueKind == JsonValueKind.Object)
				{
					int stable2 = IntValue(write, "suggested_write_concurrency", 0);
					if (stable2 >= 350)
					{
						int writeSuggested = StableToDailyWriteConcurrency(stable2);
						string writeFinished = StringValue(write, "finished_at", "");
						writeText = $"后台压测工具已启用逐商品落盘；当前设置={_writeConcurrency:0}；正式结果已验证稳定写入并发 {stable2}，日常建议 {writeSuggested}{((writeFinished.Length > 0) ? ("，时间 " + ShortDate(writeFinished)) : "")}。";
					}
				}
				if (results.TryGetProperty("read", out var read) && read.ValueKind == JsonValueKind.Object)
				{
					int suggested = IntValue(read, "suggested_read_concurrency", 2);
					string finished = StringValue(read, "finished_at", "");
					return $"并发实测：上次只读建议读取并发 {suggested}{((finished.Length > 0) ? ("，时间 " + ShortDate(finished)) : "")}；{writeText}。";
				}
				return "并发实测：读取并发未实测；" + writeText + "。";
			}
			catch
			{
				return $"并发实测：后台压测工具已启用逐商品落盘；当前设置={_writeConcurrency:0}；真实测试线程最新回传：350 两次稳定，日常建议 {300}-{320}。";
			}
		}

		private async Task SaveStoreAliasFromSettingsAsync(SettingsDialog dialog)
		{
			SettingsDialog? dialog2 = dialog;
			string accountId = dialog2.SelectedAliasAccountId;
			string alias = dialog2.StoreAliasText;
			if (accountId.Length == 0)
			{
				dialog2.SetAliasStatus("没有可设置的授权账号。");
				return;
			}
			if (alias.Length == 0)
			{
			dialog2.SetAliasStatus("请输入店铺名称，例如：湖南。");
				return;
			}
			_storeAliases[accountId] = alias;
			await RunUiTaskAsync("保存店铺名称", async delegate
			{
				using (await PostJsonAsync("/api/settings", new
				{
					authDir = dialog2.AuthDir,
					outputDir = dialog2.OutputDir,
					sellerDefaultDiscount = dialog2.SellerDiscount,
					officialDefaultDiscount = dialog2.OfficialDiscount,
					readConcurrency = dialog2.ReadConcurrency,
					previewConcurrency = dialog2.PreviewConcurrency,
					writeConcurrency = dialog2.WriteConcurrency,
					storeAliases = _storeAliases,
					operatingSites = dialog2.OperatingSites,
					defaultFilters = BuildFilters()
				}))
				{
					await LoadSettingsAsync();
					await LoadAccountsAsync();
					dialog2.ReloadAliasAccounts(_accounts, _storeAliases);
					dialog2.SetAccountSummary(StoreListText(), AuthorizedAccountsText());
					dialog2.SetAliasStatus("店铺名称已保存。");
				}
			});
		}

		private async Task StartOAuthAuthorizationFromConfigAsync(SettingsDialog? dialog = null)
		{
			SettingsDialog? dialog2 = dialog;
			await RunUiTaskAsync("新增账号授权", async delegate
			{
				using ApiJson doc = await PostJsonAsync("/api/oauth/start/from-config", new { });
				if (!Bool(doc.Root, "ok"))
				{
					throw new InvalidOperationException(StringValue(doc.Root, "error", $"授权链接生成失败：HTTP {doc.StatusCode}"));
				}
				string url = StringValue(doc.Root, "authorizationUrl", "");
				if (url.Length == 0)
				{
					throw new InvalidOperationException("后端未返回授权链接。");
				}
				string warning = StringValue(doc.Root, "warning", "");
				if (warning.Length > 0)
				{
					Log("授权提示：" + warning);
				}
				string message = "授权链接已复制，请粘贴到目标账号已登录的浏览器中打开。授权完成后复制最终回调地址或 code，回到本程序点“粘贴授权结果”。";
				if (TryCopyToClipboard(url))
				{
					Log(message);
					dialog2?.SetAuthorizationStatus(message);
					return;
				}
				dialog2?.SetAuthorizationStatus("剪贴板复制失败，请在弹窗中手动复制授权链接。");
				using AuthorizationLinkDialog linkDialog = new AuthorizationLinkDialog(url);
				IWin32Window win32Window2;
				if (dialog2 == null)
				{
					IWin32Window win32Window = this;
					win32Window2 = win32Window;
				}
				else
				{
					IWin32Window win32Window = dialog2;
					win32Window2 = win32Window;
				}
				IWin32Window owner = win32Window2;
				linkDialog.ShowDialog(owner);
			});
		}

		private async Task CompleteOAuthAuthorizationFromCallbackAsync(SettingsDialog? dialog = null)
		{
			SettingsDialog? dialog2 = dialog;
			string callbackText;
			if (dialog2 != null)
			{
				callbackText = dialog2.CallbackText;
			}
			else
			{
				using OAuthCallbackDialog input = new OAuthCallbackDialog();
				if (input.ShowDialog(this) != DialogResult.OK)
				{
					Log("授权结果未提交。");
					return;
				}
				callbackText = input.CallbackText;
			}
			if (callbackText.Length == 0)
			{
				dialog2?.SetAuthorizationStatus("请先粘贴回调链接或 code。");
				Log("授权结果未提交：缺少回调链接或 code。");
				return;
			}
			await RunUiTaskAsync("完成账号授权", async delegate
			{
				using ApiJson doc = await PostJsonAsync("/api/oauth/complete-callback", new
				{
					callbackUrl = callbackText
				});
				if (!Bool(doc.Root, "ok"))
				{
					throw new InvalidOperationException(StringValue(doc.Root, "error", $"授权完成失败：HTTP {doc.StatusCode}"));
				}
				string accountName = "新账号";
				if (doc.Root.TryGetProperty("account", out var account))
				{
					accountName = AccountDisplayName(account, StringValue(account, "account_id", ""));
				}
				Log("授权完成：" + accountName);
				await LoadAccountsAsync();
				dialog2?.SetAccountSummary(StoreListText(), AuthorizedAccountsText());
				dialog2?.SetAuthorizationStatus("账号授权已保存，账号/店铺摘要已刷新。");
				MessageBox.Show("账号授权已保存。", "账号授权", MessageBoxButtons.OK, MessageBoxIcon.Asterisk);
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
				SetBusy(busy: true, label + "...");
				await action();
			}
			catch (Exception ex)
			{
				string message = ProductFacingErrorMessage(ex);
				Log(label + "失败：" + message);
				AppendInternalDiagnostic("UI task failed: " + label, ex);
				if (!IsBusinessTimeoutOrRequestError(ex))
				{
					MessageBox.Show(message, "美客多折扣管家", MessageBoxButtons.OK, MessageBoxIcon.Hand);
				}
			}
			finally
			{
				SetBusy(busy: false, WorkbenchReadyText);
			}
		}

		private async Task<List<OperatingSiteOption>> LoadOperatingSiteOptionsAsync()
		{
			List<OperatingSiteOption> options = new List<OperatingSiteOption>();
			foreach (AccountInfo account in _accounts)
			{
				try
				{
					using JsonDocument doc = await GetJsonAsync("/api/accounts/" + Uri.EscapeDataString(account.AccountId) + "/sites?includeAll=1&probeBusiness=1");
					if (!doc.RootElement.TryGetProperty("sites", out var sites) || sites.ValueKind != JsonValueKind.Array)
					{
						continue;
					}
					foreach (JsonElement site in sites.EnumerateArray())
					{
						string siteId = StringValue(site, "site_id", "");
						if (siteId.Length == 0)
						{
							continue;
						}
						int activeListings = IntValue(site, "active_listing_count", 0);
						string evidence = activeListings > 0 ? $"活跃商品 {activeListings} 个" : "未读取到活跃商品，请确认是否经营";
						options.Add(new OperatingSiteOption(account.AccountId, account.StoreName, siteId, SiteDisplayName(siteId), Bool(site, "suggested_operating"), Bool(site, "configured"), Bool(site, "operating"), evidence));
					}
				}
				catch (Exception ex)
				{
					AppendInternalDiagnostic("operating site inspection failed", ex);
				}
			}
			return options
				.GroupBy((OperatingSiteOption option) => option.AccountId + "|" + option.SiteId, StringComparer.OrdinalIgnoreCase)
				.Select((IGrouping<string, OperatingSiteOption> group) => group.First())
				.OrderBy((OperatingSiteOption option) => option.StoreName)
				.ThenBy((OperatingSiteOption option) => option.SiteId)
				.ToList();
		}

		private object BuildFilters()
		{
			string siteId = SelectedValue(_siteSelect);
			string sellerPromotion = SelectedValue(_sellerActivitySelect);
			string officialPromotion = SelectedValue(_officialActivitySelect);
			bool excludeSeller = string.Equals(sellerPromotion, ExcludeActivityValue, StringComparison.OrdinalIgnoreCase);
			bool excludeOfficial = string.Equals(officialPromotion, ExcludeActivityValue, StringComparison.OrdinalIgnoreCase);
			return new
			{
				siteId = siteId,
				siteIds = ((siteId.Length <= 0) ? Array.Empty<string>() : new string[1] { siteId }),
				promotionTypes = Array.Empty<string>(),
				keywords = Array.Empty<string>(),
				sellerActivityNames = ((excludeSeller || sellerPromotion.Length <= 0) ? Array.Empty<string>() : new string[1] { sellerPromotion }),
				officialActivityNames = ((excludeOfficial || officialPromotion.Length <= 0) ? Array.Empty<string>() : new string[1] { officialPromotion }),
				excludeSeller = excludeSeller,
				excludeOfficial = excludeOfficial
			};
		}

		private string ResolveAccountIdForStore(string storeKey)
		{
			if (!_storeAccountIds.TryGetValue(storeKey, out List<string>? accountIds) || accountIds.Count <= 0)
			{
				return _accounts.FirstOrDefault()?.AccountId ?? "";
			}
			return accountIds[0];
		}

		private IReadOnlyList<string> ResolveAccountIdsForStore(string storeKey)
		{
			if (_storeAccountIds.TryGetValue(storeKey, out List<string>? accountIds) && accountIds.Count > 0)
			{
				return accountIds.Distinct<string>(StringComparer.OrdinalIgnoreCase).ToList();
			}
			return _accounts.Select((AccountInfo account) => account.AccountId).Distinct<string>(StringComparer.OrdinalIgnoreCase).ToList();
		}

		private IReadOnlyList<string> SelectedAccountIds()
		{
			return ResolveAccountIdsForStore((_selectedStoreKey.Length > 0) ? _selectedStoreKey : "all");
		}

		private string StoreNameForAccountId(string accountId)
		{
			string accountId2 = accountId;
			AccountInfo? account = _accounts.FirstOrDefault((AccountInfo item) => string.Equals(item.AccountId, accountId2, StringComparison.OrdinalIgnoreCase));
			if (account != null)
			{
				return account.StoreName;
			}
			string knownStore = KnownStoreNameForAccountId(accountId2);
			if (knownStore.Length <= 0)
			{
				return "店铺待命名";
			}
			return knownStore;
		}

		private string StoreListText()
		{
			string[] stores = (from name in _accounts.Select((AccountInfo account) => account.StoreName).Distinct<string>(StringComparer.OrdinalIgnoreCase)
				orderby name
				select name).ToArray();
			if (stores.Length != 0)
			{
				return string.Join("、", stores);
			}
			return "无";
		}

		private string AuthorizedAccountsText()
		{
			if (_accounts.Count == 0)
			{
				return "无";
			}
			string[] sites = (from site in (from account in _accounts
					select account.SiteId into site
					where site.Length > 0
					select site).Distinct<string>(StringComparer.OrdinalIgnoreCase)
				orderby site
				select site).ToArray();
			if (sites.Length == 0)
			{
				return $"已授权 {_accounts.Count} 个账号";
			}
			return $"已授权 {_accounts.Count} 个账号，站点：{string.Join("、", sites)}";
		}

		private static string InferStoreName(string displayName)
		{
			return "店铺待命名";
		}

		private static string KnownStoreNameForAccountId(string accountId)
		{
			return "";
		}

		private void RenderTodayDecision(JsonElement decision)
		{
			string action = StringValue(decision, "action", StringValue(decision, "today_action", "-"));
			string reason = StringValue(decision, "reason", "-");
			int selected = IntValue(decision, "selected_promotions", 0);
			int total = IntValue(decision, "promotions_total", 0);
			bool completed = Bool(decision, "already_completed");
			_todayLabel.Text = $"今日判断：{(completed ? "今日已完成" : LegacyActionText(action))} | 活动 {selected}/{total} | {reason}";
		}

		private void RenderExecutionResult(int statusCode, JsonElement root)
		{
			if (root.ValueKind != JsonValueKind.Object)
			{
				Log("提交执行已结束：后台没有返回完整汇总，已保存的结果请查看历史记录。");
				return;
			}
			JsonElement execution;
			bool num = root.TryGetProperty("execution", out execution);
			if (!num && root.TryGetProperty("prepare", out var prepare))
			{
				RenderPrepareStages(prepare);
			}
			if (num)
			{
				int val = IntValue(execution, "total", 0);
				int success = IntValue(execution, "success", 0);
				int failed = IntValue(execution, "failed", 0);
				int skipped = IntValue(execution, "skipped", 0);
				int blocked = IntValue(execution, "blocked", 0);
				int displayTotal = Math.Max(val, success + failed + skipped);
				string action = StringValue(root, "action", "-");
				string statusText = (Bool(root, "ok") ? "提交执行完成" : "提交执行已停止/部分完成");
				Log($"{statusText}：{LegacyActionText(action)}，商品 {displayTotal}，成功 {success}，失败 {failed}，跳过 {skipped}，阻断活动 {blocked}。");
				if (failed > 0 || blocked > 0)
				{
					Log("请在批次表查看失败原因。");
				}
				if (root.TryGetProperty("today_decision", out var decision))
				{
					RenderTodayDecision(decision);
				}
				return;
			}
			if (root.TryGetProperty("confirmation_package", out var package))
			{
				string status = StringValue(package, "status", "-");
				int planned = IntValue(package, "planned", 0);
				int skipped2 = IntValue(package, "skipped", 0);
				int blocked2 = IntValue(package, "blocked", 0);
				Log($"后端返回执行确认信息：HTTP {statusCode}，状态 {status}，可执行 {planned}，跳过 {skipped2}，阻断 {blocked2}。");
				if (package.TryGetProperty("blocking_reasons", out var reasons) && reasons.ValueKind == JsonValueKind.Array)
				{
					foreach (JsonElement item in reasons.EnumerateArray())
					{
						Log("阻断原因：" + item.GetString());
					}
				}
				if (package.TryGetProperty("expected_impact_summary", out var impact))
				{
					Log("预计影响：" + impact.GetString());
				}
				using ConfirmationPackageForm form = new ConfirmationPackageForm(package);
				form.ShowDialog(this);
				return;
			}
			bool ok = Bool(root, "ok");
			string message = StringValue(root, "message", StringValue(root, "error", $"HTTP {statusCode}"));
			Log(ok ? ("执行结果：" + message) : ("提交执行失败：" + message));
		}

		private static string TerminalJobMessage(string status, string error)
		{
			string cleanError = FriendlyExecutionErrorMessage(error);
			if (status.Equals("cancelled", StringComparison.OrdinalIgnoreCase))
			{
				return "任务已按停止规则结束，已保存已完成结果，请查看历史记录。" + ((cleanError.Length > 0) ? (" 原因：" + cleanError) : "");
			}
			if (status.Equals("failed", StringComparison.OrdinalIgnoreCase))
			{
				return "任务未完整完成，已保存已完成结果，请查看历史记录。" + ((cleanError.Length > 0) ? (" 原因：" + cleanError) : "");
			}
			if (cleanError.Length <= 0)
			{
				return "提交执行已结束，请查看历史记录。";
			}
			return "提交执行结束：" + cleanError;
		}

		private static string FriendlyExecutionErrorMessage(Exception ex)
		{
			return FriendlyExecutionErrorMessage(ex.Message);
		}

		private static string FriendlyExecutionErrorMessage(string message)
		{
			if (string.IsNullOrWhiteSpace(message))
			{
				return "";
			}
			if (message.Contains("requires an element of type 'Object'", StringComparison.OrdinalIgnoreCase) || message.Contains("target element has type 'Null'", StringComparison.OrdinalIgnoreCase) || message.Contains("JsonElement", StringComparison.OrdinalIgnoreCase))
			{
				return "任务已结束，但后台没有返回完整汇总；已完成结果已保存，请查看历史记录。";
			}
			return message.Replace("The requested operation requires an element of type 'Object', but the target element has type 'Null'.", "任务已结束，但后台没有返回完整汇总；已完成结果已保存，请查看历史记录。", StringComparison.OrdinalIgnoreCase).Trim();
		}

		private void RenderPrepareStages(JsonElement prepare)
		{
			if (prepare.TryGetProperty("promotions", out var promotions))
			{
				foreach (string line2 in StringArray(promotions, "stages"))
				{
					Log("准备活动：" + line2);
				}
			}
			if (!prepare.TryGetProperty("items", out var items))
			{
				return;
			}
			foreach (string line in StringArray(items, "stages"))
			{
				Log("准备商品：" + line);
			}
		}

		private string SelectedSubmitAction()
		{
			string mode = _modeSelect.SelectedItem?.ToString() ?? "";
			if (mode.Contains("报活动"))
			{
				return "enroll";
			}
			if (mode.Contains("更新"))
			{
				return "update";
			}
			if (mode.Contains("取消"))
			{
				return "cancel";
			}
			return "";
		}

		private string SelectedSubmitModeText()
		{
			return _modeSelect.SelectedItem?.ToString() ?? "自动判断";
		}

		private string ExecutionStartLogText(string action)
		{
			string scope = $"店铺={SelectedComboText(_accountSelect)}，站点={SelectedComboText(_siteSelect)}";
			if (string.Equals(action, "cancel", StringComparison.OrdinalIgnoreCase))
			{
				return $"开始{SelectedSubmitModeText()}：{scope}，取消不使用折扣。";
			}
			return $"开始{SelectedSubmitModeText()}：{scope}，自建{_sellerDiscount.Value:0}%，官方{_officialDiscount.Value:0}%。";
		}

		private void UpdateDiscountInputState(bool busy = false)
		{
			bool cancelMode = string.Equals(SelectedSubmitAction(), "cancel", StringComparison.OrdinalIgnoreCase);
			bool autoCancel = string.IsNullOrWhiteSpace(SelectedSubmitAction()) && string.Equals(_autoResolvedAction, "cancel", StringComparison.OrdinalIgnoreCase);
			bool enabled = !busy && !cancelMode && !autoCancel;
			_sellerDiscount.Enabled = enabled;
			_officialDiscount.Enabled = enabled;
		}

		private static string SelectedComboText(ComboBox combo)
		{
			if (!(combo.SelectedItem is ComboItem item))
			{
				return combo.Text;
			}
			return item.Text;
		}

		private async Task<ApiJson> PostJsonAsync(string path, object body)
		{
			await EnsureServiceReadyForUiAsync();
			string json = JsonSerializer.Serialize(body);
			HttpRequestMessage request = new HttpRequestMessage(HttpMethod.Post, path)
			{
				Content = new StringContent(json, Encoding.UTF8, "application/json")
			};
			try
			{
				using HttpResponseMessage response = await SendWithServiceErrorAsync((CancellationToken token) => _http.SendAsync(request, token), path, RequestTimeoutFor(path, isPost: true));
				string text = await response.Content.ReadAsStringAsync();
				return new ApiJson((int)response.StatusCode, JsonDocument.Parse(string.IsNullOrWhiteSpace(text) ? "{}" : text));
			}
			finally
			{
				if (request != null)
				{
					((IDisposable)request).Dispose();
				}
			}
		}

		private async Task<JsonDocument> GetJsonAsync(string path)
		{
			await EnsureServiceReadyForUiAsync();
			HttpRequestMessage request = new HttpRequestMessage(HttpMethod.Get, path);
			try
			{
				using HttpResponseMessage response = await SendWithServiceErrorAsync((CancellationToken token) => _http.SendAsync(request, token), path, RequestTimeoutFor(path, isPost: false));
				string text = await response.Content.ReadAsStringAsync();
				if (!response.IsSuccessStatusCode)
				{
					throw new InvalidOperationException((text.Length > 0) ? text : $"请求失败：HTTP {response.StatusCode}");
				}
				return JsonDocument.Parse(string.IsNullOrWhiteSpace(text) ? "{}" : text);
			}
			finally
			{
				if (request != null)
				{
					((IDisposable)request).Dispose();
				}
			}
		}

		private async Task EnsureServiceReadyForUiAsync()
		{
			if (await WaitUntilHealthyAsync(TimeSpan.FromSeconds(2.0)))
			{
				return;
			}
			Exception? lastError = null;
			if (_serviceWarmupTask != null)
			{
				try
				{
					Process? warmupProcess = await _serviceWarmupTask;
					if (warmupProcess != null)
					{
						_startedService = warmupProcess;
					}
				}
				catch (Exception ex)
				{
					lastError = ex;
					AppendInternalDiagnostic("service warmup failed", ex);
				}
				if (await WaitUntilHealthyAsync(TimeSpan.FromSeconds(8.0)))
				{
					return;
				}
			}
			for (int attempt = 0; attempt < 2; attempt++)
			{
				try
				{
					if (attempt > 0)
					{
						SetBusy(busy: true, WorkbenchRepairingText);
					}
					Process? process = await EnsureServiceAsync();
					if (process != null)
					{
						_startedService = process;
					}
				}
				catch (Exception ex2)
				{
					lastError = ex2;
					AppendInternalDiagnostic("service repair attempt failed", ex2);
				}
				if (await WaitUntilHealthyAsync(TimeSpan.FromSeconds(12.0)))
				{
					return;
				}
				await Task.Delay(800);
			}
			throw new InvalidOperationException(BuildServiceUnavailableMessage(lastError));
		}

		private static async Task<bool> WaitUntilHealthyAsync(TimeSpan timeout)
		{
			DateTime deadline = DateTime.UtcNow.Add(timeout);
			do
			{
				if (await IsHealthy())
				{
					return true;
				}
				await Task.Delay(350);
			}
			while (DateTime.UtcNow < deadline);
			return false;
		}

		private async Task<HttpResponseMessage> SendWithServiceErrorAsync(Func<CancellationToken, Task<HttpResponseMessage>> send, string path, TimeSpan timeout)
		{
			using CancellationTokenSource cts = new CancellationTokenSource(timeout);
			try
			{
				return await send(cts.Token);
			}
			catch (HttpRequestException ex2)
			{
				if (await IsHealthy())
				{
					throw new InvalidOperationException(BuildBusinessRequestMessage(path, ex2), ex2);
				}
				throw new InvalidOperationException(BuildServiceUnavailableMessage(ex2), ex2);
			}
			catch (OperationCanceledException ex)
			{
				if (await IsHealthy())
				{
					throw new InvalidOperationException(BuildBusinessTimeoutMessage(path, timeout), ex);
				}
				throw new InvalidOperationException(BuildServiceUnavailableMessage(ex), ex);
			}
		}

		private static TimeSpan RequestTimeoutFor(string path, bool isPost)
		{
			if (path.Contains("/api/health", StringComparison.OrdinalIgnoreCase))
			{
				return TimeSpan.FromSeconds(5.0);
			}
			if (path.Contains("/api/today/execute", StringComparison.OrdinalIgnoreCase) || path.Contains("/api/batch/execute", StringComparison.OrdinalIgnoreCase) || path.Contains("/api/execute", StringComparison.OrdinalIgnoreCase) || path.Contains("/api/cancel/filtered/precheck", StringComparison.OrdinalIgnoreCase) || path.Contains("/api/batch/items/fetch", StringComparison.OrdinalIgnoreCase) || (path.Contains("/api/accounts/", StringComparison.OrdinalIgnoreCase) && path.Contains("/promotions/fetch", StringComparison.OrdinalIgnoreCase)) || path.Contains("/api/inventory-fallback/", StringComparison.OrdinalIgnoreCase))
			{
				return TimeSpan.FromMinutes(10.0);
			}
			if (path.Contains("/api/oauth/complete-callback", StringComparison.OrdinalIgnoreCase))
			{
				return TimeSpan.FromMinutes(3.0);
			}
			if (!isPost)
			{
				return TimeSpan.FromSeconds(45.0);
			}
			return TimeSpan.FromSeconds(90.0);
		}

		private static string BuildServiceUnavailableMessage(Exception? error)
		{
			if (error != null)
			{
				AppendInternalDiagnostic("workbench component unavailable", error);
			}
			return "程序组件暂时不可用，已尝试自动修复但没有成功。\r\n请关闭软件后重新打开；如果仍然出现，请把诊断信息发给我处理。";
		}

		private static string BuildBusinessTimeoutMessage(string path, TimeSpan timeout)
		{
			return $"当前操作等待时间较长，已等待 {FormatTimeout(timeout)}。\r\n可能正在读取活动/商品或等待 Mercado 返回。请稍后点“刷新”查看批次结果；如果重复出现，请缩小店铺、站点或活动范围后再试。";
		}

		private static string BuildBusinessRequestMessage(string path, Exception error)
		{
			return "当前操作没有完成。\r\n原因：" + ProductFacingErrorMessage(error);
		}

		private static bool IsBusinessTimeoutOrRequestError(Exception ex)
		{
			string message = ex.Message;
			if (message.StartsWith("当前操作等待时间较长", StringComparison.Ordinal) || message.StartsWith("当前操作没有完成", StringComparison.Ordinal))
			{
				return true;
			}
			if (!message.StartsWith("业务请求超时：", StringComparison.Ordinal))
			{
				return message.StartsWith("业务请求失败：", StringComparison.Ordinal);
			}
			return true;
		}

		private static string FormatTimeout(TimeSpan timeout)
		{
			if (timeout.TotalMinutes >= 1.0)
			{
				return $"{timeout.TotalMinutes:0.#} 分钟";
			}
			return $"{timeout.TotalSeconds:0} 秒";
		}

		private void SetBusy(bool busy, string status)
		{
			_statusLabel.Text = status;
			Control[] array = new Control[6] { _submitButton, _settingsButton, _loadActivitiesButton, _decisionButton, _previewButton, _refreshTasksButton };
			for (int i = 0; i < array.Length; i++)
			{
				array[i].Enabled = !busy;
			}
			UpdateDiscountInputState(busy);
		}

		private void SetExecutionBusy(bool busy)
		{
			_executionJobRunning = busy;
			if (busy)
			{
				SetBusy(busy: true, "提交执行...");
				_submitButton.Enabled = true;
				_submitButton.Text = "停止";
			}
			else
			{
				_submitButton.Text = "提交执行";
				SetBusy(busy: false, WorkbenchReadyText);
			}
		}

		private void ConfigureGrid()
		{
			_taskGrid.Dock = DockStyle.Fill;
			_taskGrid.AllowUserToAddRows = false;
			_taskGrid.AllowUserToDeleteRows = false;
			_taskGrid.ReadOnly = true;
			_taskGrid.RowHeadersVisible = false;
			_taskGrid.Font = new Font("Microsoft YaHei", 10f, FontStyle.Regular);
			_taskGrid.SelectionMode = DataGridViewSelectionMode.FullRowSelect;
			_taskGrid.MultiSelect = true;
			_taskGrid.ClipboardCopyMode = DataGridViewClipboardCopyMode.EnableWithoutHeaderText;
			_taskGrid.AutoSizeColumnsMode = DataGridViewAutoSizeColumnsMode.None;
			_taskGrid.AutoSizeRowsMode = DataGridViewAutoSizeRowsMode.None;
			_taskGrid.CellBorderStyle = DataGridViewCellBorderStyle.SingleHorizontal;
			_taskGrid.ColumnHeadersBorderStyle = DataGridViewHeaderBorderStyle.Single;
			_taskGrid.BackgroundColor = UiTheme.TableBackground;
			_taskGrid.BorderStyle = BorderStyle.None;
			_taskGrid.GridColor = UiTheme.NormalBorder;
			_taskGrid.EnableHeadersVisualStyles = false;
			_taskGrid.ColumnHeadersHeightSizeMode = DataGridViewColumnHeadersHeightSizeMode.DisableResizing;
			_taskGrid.ColumnHeadersHeight = 38;
			_taskGrid.ColumnHeadersDefaultCellStyle.BackColor = UiTheme.CardBackground;
			_taskGrid.ColumnHeadersDefaultCellStyle.ForeColor = UiTheme.MainText;
			_taskGrid.ColumnHeadersDefaultCellStyle.SelectionBackColor = UiTheme.CardBackground;
			_taskGrid.ColumnHeadersDefaultCellStyle.SelectionForeColor = UiTheme.MainText;
			_taskGrid.ColumnHeadersDefaultCellStyle.Font = new Font("Microsoft YaHei", 10f, FontStyle.Bold);
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
			AddGridColumn("time", "时间", 116);
			AddGridColumn("action", "动作", 92);
			AddGridColumn("seller", "自建活动", 90);
			AddGridColumn("official", "官方活动", 90);
			AddGridColumn("type", "类型", 66);
			AddGridColumn("qtyType", "数量类型", 102);
			AddGridColumn("total", "商品数", 74);
			AddGridColumn("success", "成功", 74);
			AddGridColumn("failed", "失败", 74);
			DataGridViewTextBoxColumn reason = new DataGridViewTextBoxColumn
			{
				Name = "reason",
				HeaderText = "失败原因",
				AutoSizeMode = DataGridViewAutoSizeColumnMode.None,
				MinimumWidth = 220,
				Width = 220
			};
			_taskGrid.Columns.Add(reason);
			ConfigureTaskContextMenu();
			_taskGrid.CellDoubleClick += async delegate(object? _, DataGridViewCellEventArgs e)
			{
				if (e.RowIndex >= 0)
				{
					await ShowSelectedTaskDetailsAsync();
				}
			};
			_taskGrid.SelectionChanged += delegate
			{
				ShowSelectedTaskSummaryInLog();
			};
			ShowEmptyTasksRow("正在读取批次记录...");
		}

		private void ConfigureTaskContextMenu()
		{
			_taskMenu.Items.Clear();
			_taskMenu.BackColor = UiTheme.CardBackground;
			_taskMenu.ForeColor = UiTheme.MainText;
			_taskMenu.Renderer = new ToolStripProfessionalRenderer(new DarkMenuColorTable());
			_taskMenu.ShowImageMargin = false;
			_taskMenu.Items.Add("查看详情", null, async delegate
			{
				await ShowSelectedTaskDetailsAsync();
			});
			_taskMenu.Items.Add("复制详情", null, async delegate
			{
				await CopySelectedTaskDetailsAsync();
			});
			_taskMenu.Items.Add(new ToolStripSeparator());
			_taskMenu.Items.Add("复制选中行", null, delegate
			{
				CopySelectedTaskRows();
			});
			_taskMenu.Items.Add("删除选中记录", null, async delegate
			{
				await DeleteSelectedTaskRowsAsync();
			});
			_taskMenu.Items.Add("刷新记录", null, async delegate
			{
				await RefreshTasksAsync();
			});
			_taskGrid.ContextMenuStrip = _taskMenu;
			_taskGrid.MouseDown += delegate(object? _, MouseEventArgs e)
			{
				if (e.Button == MouseButtons.Right)
				{
					DataGridView.HitTestInfo hitTestInfo = _taskGrid.HitTest(e.X, e.Y);
					if (hitTestInfo.RowIndex >= 0 && !_taskGrid.Rows[hitTestInfo.RowIndex].Selected)
					{
						_taskGrid.ClearSelection();
						_taskGrid.Rows[hitTestInfo.RowIndex].Selected = true;
					}
				}
			};
			_taskGrid.KeyDown += async delegate(object? _, KeyEventArgs e)
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
				AutoSizeMode = DataGridViewAutoSizeColumnMode.None,
				Width = width,
				MinimumWidth = 42,
				SortMode = DataGridViewColumnSortMode.NotSortable
			});
		}

		private void ApplyTaskGridColumnWidths()
		{
			if (_taskGrid.IsDisposed || _taskGrid.Columns.Count == 0 || !_taskGrid.Columns.Contains("reason"))
			{
				return;
			}
			DataGridViewColumn reason = _taskGrid.Columns["reason"];
			int fixedWidth = _taskGrid.Columns.Cast<DataGridViewColumn>()
				.Where((DataGridViewColumn column) => !ReferenceEquals(column, reason) && column.Visible)
				.Sum((DataGridViewColumn column) => column.Width);
			int available = Math.Max(0, _taskGrid.ClientSize.Width - SystemInformation.VerticalScrollBarWidth - 2);
			reason.Width = Math.Max(reason.MinimumWidth, available - fixedWidth);
		}

		private void ShowEmptyTasksRow(string message)
		{
			_taskGrid.Rows.Clear();
			int row = _taskGrid.Rows.Add("", "等待操作", "", "", "-", "-", "", "", "", message);
			_taskGrid.Rows[row].DefaultCellStyle.ForeColor = UiTheme.WeakText;
		}

		private void Log(string message)
		{
			if (_logBox.TextLength > 0)
			{
				_logBox.AppendText(Environment.NewLine);
			}
			_logBox.AppendText($"[{DateTime.Now:HH:mm:ss}] {message}");
		}

		private static void ConfigureCombo(ComboBox combo, int width)
		{
			combo.DropDownStyle = ComboBoxStyle.DropDownList;
			combo.Width = width;
			combo.Font = new Font("Microsoft YaHei", 10f, FontStyle.Regular);
			combo.Dock = DockStyle.Fill;
			combo.Margin = new Padding(2, 2, 6, 2);
			UiTheme.StyleCombo(combo);
			combo.DropDownWidth = width;
		}

		private static void UpdateComboDropDownWidth(ComboBox combo)
		{
			int maxTextWidth = combo.Width;
			foreach (object item in combo.Items)
			{
				int width = TextRenderer.MeasureText(item.ToString(), combo.Font).Width + 36;
				if (width > maxTextWidth)
				{
					maxTextWidth = width;
				}
			}
			Screen screen = Screen.FromControl(combo);
			int maxWidth = Math.Min(780, Math.Max(360, screen.WorkingArea.Width - 80));
			combo.DropDownWidth = Math.Max(combo.Width, Math.Min(maxTextWidth, maxWidth));
		}

		private static void ConfigureNumber(NumericUpDown number, decimal value)
		{
			number.Minimum = 1m;
			number.Maximum = 90m;
			number.DecimalPlaces = 0;
			number.Increment = 1m;
			number.Value = value;
			number.Width = 56;
			number.Font = new Font("Microsoft YaHei", 10f, FontStyle.Regular);
			number.Dock = DockStyle.Fill;
			number.Margin = new Padding(2, 2, 6, 2);
			UiTheme.StyleNumber(number);
		}

		private static void ConfigureButton(Button button, string text, int width, bool primary)
		{
			button.Text = text;
			button.Width = width;
			button.Height = 31;
			button.Font = new Font("Microsoft YaHei", 10f, FontStyle.Regular);
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
				Font = new Font("Microsoft YaHei", 10f, FontStyle.Regular),
				ForeColor = UiTheme.MainText,
				BackColor = Color.Transparent
			}, column, 0);
		}

		private static string SelectedValue(ComboBox combo)
		{
			if (!(combo.SelectedItem is ComboItem item))
			{
				return "";
			}
			return item.Value;
		}

		private static void SelectComboByValue(ComboBox combo, string value)
		{
			for (int i = 0; i < combo.Items.Count; i++)
			{
				if (combo.Items[i] is ComboItem item && item.Value == value)
				{
					combo.SelectedIndex = i;
					break;
				}
			}
		}

		private static void ReplaceComboItemText(ComboBox combo, string value, string text)
		{
			for (int i = 0; i < combo.Items.Count; i++)
			{
				if (combo.Items[i] is ComboItem item && item.Value == value)
				{
					bool num = combo.SelectedIndex == i;
					combo.Items[i] = new ComboItem(value, text);
					if (num)
					{
						combo.SelectedIndex = i;
					}
					break;
				}
			}
		}

		private static string AccountDisplayName(JsonElement account, string accountId)
		{
			string name = StringValue(account, "nickname", "");
			if (name.Length == 0)
			{
				name = StringValue(account, "display_name", "");
			}
			if (name.StartsWith("Standalone ", StringComparison.OrdinalIgnoreCase))
			{
				name = "";
			}
			if (name.Length == 0 || string.Equals(name, accountId, StringComparison.OrdinalIgnoreCase))
			{
				name = "账号";
			}
			if (!(name == "账号"))
			{
				return name + "（" + accountId + "）";
			}
			return "账号 " + accountId;
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
				_ => "站点 " + siteId, 
			};
		}

		private static string PromotionTypeDisplayName(string type)
		{
			string text = type.ToUpperInvariant();
			switch (text)
			{
			default:
				if (text.Length != 0)
				{
					break;
				}
				return "活动";
			case "SELLER_CAMPAIGN":
				return "自建活动";
			case "DEAL":
				return "官方活动";
			case "SMART":
				return "智能折扣";
			case "LIGHTNING":
				return "限时秒杀";
			case null:
				break;
			}
			return type;
		}

		private static string ActivityDisplayName(JsonElement promotion)
		{
			string name = StringValue(promotion, "name", "");
			if (name.Length > 0)
			{
				return NormalizeActivityDisplayName(name);
			}
			string type = StringValue(promotion, "promotion_type", "");
			string promotionId = StringValue(promotion, "promotion_id", "");
			if (promotionId.Length > 0)
			{
				return PromotionTypeDisplayName(type) + " " + promotionId;
			}
			return NormalizeActivityDisplayName(StringValue(promotion, "id", ""));
		}

		private static void AddActivityChoice(Dictionary<string, ActivityChoice> choices, string key, string displayName)
		{
			if (!choices.TryGetValue(key, out ActivityChoice? existing))
			{
				choices[key] = new ActivityChoice(key, displayName, 1);
				return;
			}
			existing.Count++;
			if (IsBetterActivityDisplay(displayName, existing.DisplayName))
			{
				existing.DisplayName = displayName;
			}
		}

		private static bool IsBetterActivityDisplay(string candidate, string current)
		{
			if (string.IsNullOrWhiteSpace(current))
			{
				return true;
			}
			if (string.IsNullOrWhiteSpace(candidate))
			{
				return false;
			}
			return candidate.Length < current.Length;
		}

		private static string NormalizeActivityDisplayName(string value)
		{
			return Regex.Replace(RemoveInvisibleCharacters(value.Normalize(NormalizationForm.FormKC)), "\\s+", " ").Trim().TrimEnd(' ', '.', ',', ';', ':', '|', '/', '\\', '，', '。', '；', '：', '、');
		}

		private static string NormalizeActivityNameKey(string value)
		{
			return NormalizeActivityDisplayName(value).ToLowerInvariant();
		}

		private static string RemoveInvisibleCharacters(string value)
		{
			StringBuilder builder = new StringBuilder(value.Length);
			foreach (char ch in value)
			{
				UnicodeCategory category = CharUnicodeInfo.GetUnicodeCategory(ch);
				if (category != UnicodeCategory.Format && category != UnicodeCategory.Control)
				{
					builder.Append(ch);
				}
			}
			return builder.ToString();
		}

		private static string StringValue(JsonElement element, string name, string fallback)
		{
			if (!element.TryGetProperty(name, out var value) || value.ValueKind == JsonValueKind.Null)
			{
				return fallback;
			}
			return value.ToString();
		}

		private static int IntValue(JsonElement element, string name, int fallback)
		{
			if (!element.TryGetProperty(name, out var value))
			{
				return fallback;
			}
			if (value.ValueKind == JsonValueKind.Number && value.TryGetInt32(out var number))
			{
				return number;
			}
			if (!int.TryParse(value.ToString(), out var parsed))
			{
				return fallback;
			}
			return parsed;
		}

		private static IEnumerable<int> IntArray(JsonElement element, string name)
		{
			if (!element.TryGetProperty(name, out var value) || value.ValueKind != JsonValueKind.Array)
			{
				yield break;
			}
			foreach (JsonElement item in value.EnumerateArray())
			{
				int parsed;
				if (item.ValueKind == JsonValueKind.Number && item.TryGetInt32(out var number))
				{
					yield return number;
				}
				else if (int.TryParse(item.ToString(), out parsed))
				{
					yield return parsed;
				}
			}
		}

		private static decimal DecimalValue(JsonElement element, string name, decimal fallback)
		{
			if (!element.TryGetProperty(name, out var value))
			{
				return fallback;
			}
			if (value.ValueKind == JsonValueKind.Number && value.TryGetDecimal(out var number))
			{
				return ClampNumber(number);
			}
			if (!decimal.TryParse(value.ToString(), out var parsed))
			{
				return fallback;
			}
			return ClampNumber(parsed);
		}

		private static decimal ClampNumber(decimal value)
		{
			return Math.Max(1m, Math.Min(90m, value));
		}

		private static bool Bool(JsonElement element, string name)
		{
			if (element.TryGetProperty(name, out var value))
			{
				return value.ValueKind == JsonValueKind.True;
			}
			return false;
		}

		private static IEnumerable<string> StringArray(JsonElement element, string name)
		{
			if (!element.TryGetProperty(name, out var value) || value.ValueKind != JsonValueKind.Array)
			{
				yield break;
			}
			foreach (JsonElement item in value.EnumerateArray())
			{
				string text = item.ToString();
				if (!string.IsNullOrWhiteSpace(text))
				{
					yield return text;
				}
			}
		}

		private static string ShortDate(string value)
		{
			if (!DateTime.TryParse(value, out var date))
			{
				return value;
			}
			return date.ToString("yyyy/M/d");
		}

		private static string LegacyActionText(string action)
		{
			return action switch
			{
				"enroll" => "批量报活动", 
				"update" => "批量更新", 
				"cancel" => "批量取消", 
				"completed" => "已完成", 
				_ => string.IsNullOrWhiteSpace(action) ? "-" : action, 
			};
		}

		private static string ModeDisplayName(string mode)
		{
			if (!(mode == "dry-run"))
			{
				if (mode == "real")
				{
					return "提交";
				}
				return string.IsNullOrWhiteSpace(mode) ? "-" : mode;
			}
			return "预览";
		}

		private static string QuantityText(string action)
		{
			if (!(action == "enroll"))
			{
				return "实际处理数";
			}
			return "已报名商品数";
		}

		private static string TaskQuantityNote(TaskGridRow taskRow)
		{
			JsonElement? summary = ParseTaskSummary(taskRow);
			int processed = SummaryInt(summary, "processed_total", taskRow.Success + taskRow.Failed + taskRow.Skipped);
			int candidatePool = SummaryInt(summary, "candidate_pool_total", processed);
			int apiSuccess = SummaryInt(summary, "api_success_count", taskRow.Success);
			int liveVerified = SummaryInt(summary, "live_verified_enrolled_count", -1);
			if (taskRow.QuantityText == "已报名商品数")
			{
				string source = ((liveVerified >= 0) ? $"live 回查确认 {liveVerified}，接口成功 {apiSuccess}" : $"按接口成功统计 {apiSuccess}，未做 live 复核");
				return $"数量口径：主表商品数=真实已报名/上架商品数（{source}）；候选池 {candidatePool}，实际处理 {processed}，失败 {taskRow.Failed}，跳过 {taskRow.Skipped}。";
			}
			return $"数量口径：商品数按实际处理结论合计，成功 {taskRow.Success} + 失败 {taskRow.Failed} + 跳过 {taskRow.Skipped} = 实际处理 {taskRow.Total}。";
		}

		private static JsonElement? ParseTaskSummary(TaskGridRow taskRow)
		{
			string summary = taskRow.SummaryJson;
			if (string.IsNullOrWhiteSpace(summary))
			{
				return null;
			}
			try
			{
				using JsonDocument doc = JsonDocument.Parse(summary);
				return doc.RootElement.Clone();
			}
			catch
			{
				return null;
			}
		}

		private static int SummaryInt(JsonElement? summary, string name, int fallback)
		{
			if (summary.HasValue && summary.Value.TryGetProperty(name, out var value) && value.TryGetInt32(out var parsed))
			{
				return parsed;
			}
			return fallback;
		}

		private static string TaskReason(JsonElement task)
		{
			List<string> parts = new List<string>();
			string status = StringValue(task, "status", "");
			List<string> reasons = TaskFailureReasons(task).Take(3).ToList();
			if (reasons.Count > 0)
			{
				parts.Add(string.Join("，", reasons));
			}
			int skipped = IntValue(task, "skipped_count", 0);
			if (skipped > 0)
			{
				List<string> skippedReasons = TaskSkippedReasons(task).Take(2).ToList();
				if (skippedReasons.Count > 0)
				{
					parts.Add(string.Join("，", skippedReasons));
				}
				else
				{
					parts.Add("未执行/跳过 " + skipped + " 个");
				}
			}
			int blocked = IntValue(task, "blocked_count", 0);
			if (blocked > 0)
			{
				parts.Add("阻断活动 " + blocked + " 个");
			}
			int failed = IntValue(task, "failed_count", 0);
			if (failed > 0 && reasons.Count == 0)
			{
				parts.Add("其他失败 " + failed);
			}
			if (parts.Count == 0)
			{
				if (IntValue(task, "total_count", 0) == 0)
				{
					parts.Add("未读取到可处理商品");
				}
				else if (status.Length > 0 && !string.Equals(status, "completed", StringComparison.OrdinalIgnoreCase))
				{
					parts.Add(TaskStatusDisplayName(status));
				}
			}
			return string.Join("；", parts);
		}

		private static IEnumerable<string> TaskFailureReasons(JsonElement task)
		{
			List<string> results = new List<string>();
			string summary = StringValue(task, "summary_json", "");
			if (summary.Length == 0)
			{
				return results;
			}
			try
			{
				using JsonDocument doc = JsonDocument.Parse(summary);
				if (!doc.RootElement.TryGetProperty("failure_reasons", out var reasons) || reasons.ValueKind != JsonValueKind.Array)
				{
					return results;
				}
				foreach (JsonElement item in reasons.EnumerateArray())
				{
					string text = StringValue(item, "reason", "");
					int count = IntValue(item, "count", 0);
					if (text.Length != 0)
					{
						results.Add((count > 0) ? $"{text} {count}" : text);
					}
				}
				return results;
			}
			catch
			{
				return results;
			}
		}

		private static IEnumerable<string> TaskFailureReasonDetails(JsonElement task)
		{
			if (task.TryGetProperty("full_failure_reasons", out var fullReasons) && fullReasons.ValueKind == JsonValueKind.Array)
			{
				foreach (string item in FormatFailureReasonRows(fullReasons))
				{
					yield return item;
				}
				yield break;
			}
			string summary = StringValue(task, "summary_json", "");
			if (summary.Length == 0)
			{
				yield break;
			}
			using JsonDocument doc = JsonDocument.Parse(summary);
			if (!doc.RootElement.TryGetProperty("failure_reasons", out var reasons) || reasons.ValueKind != JsonValueKind.Array)
			{
				yield break;
			}
			foreach (string item2 in FormatFailureReasonRows(reasons))
			{
				yield return item2;
			}
		}

		private static IEnumerable<string> TaskSkippedReasons(JsonElement task)
		{
			List<string> results = new List<string>();
			string summary = StringValue(task, "summary_json", "");
			if (summary.Length == 0)
			{
				return results;
			}
			try
			{
				using JsonDocument doc = JsonDocument.Parse(summary);
				if (!doc.RootElement.TryGetProperty("skipped_reasons", out var reasons) || reasons.ValueKind != JsonValueKind.Array)
				{
					return results;
				}
				foreach (JsonElement item in reasons.EnumerateArray())
				{
					string text = StringValue(item, "reason", "");
					int count = IntValue(item, "count", 0);
					if (text.Length != 0)
					{
						results.Add((count > 0) ? $"{text} {count}" : text);
					}
				}
				return results;
			}
			catch
			{
				return results;
			}
		}

		private static IEnumerable<string> TaskSkippedReasonDetails(JsonElement task)
		{
			string summary = StringValue(task, "summary_json", "");
			if (summary.Length == 0)
			{
				yield break;
			}
			using JsonDocument doc = JsonDocument.Parse(summary);
			if (!doc.RootElement.TryGetProperty("skipped_reasons", out var reasons) || reasons.ValueKind != JsonValueKind.Array)
			{
				yield break;
			}
			foreach (JsonElement reason in reasons.EnumerateArray())
			{
				string text = StringValue(reason, "reason", "");
				int count = IntValue(reason, "count", 0);
				if (text.Length != 0)
				{
					yield return (count > 0) ? $"{text}：{count}" : text;
				}
			}
		}

		private static IEnumerable<string> FormatFailureReasonRows(JsonElement reasons)
		{
			foreach (JsonElement reason in reasons.EnumerateArray())
			{
				string text = StringValue(reason, "reason", "");
				int count = IntValue(reason, "count", 0);
				if (text.Length != 0)
				{
					JsonElement sentValue;
					string sent = ((reason.TryGetProperty("sent_to_api", out sentValue) && sentValue.ValueKind == JsonValueKind.False) ? "未发送接口" : "已发送接口");
					string suggestion = StringValue(reason, "suggestion", "");
					yield return $"{text}：{count}，{sent}" + ((suggestion.Length > 0) ? ("，建议：" + suggestion) : "");
				}
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
				_ => string.IsNullOrWhiteSpace(status) ? "-" : status, 
			};
		}

		private static string TaskActivityDisplayName(JsonElement task)
		{
			string name = StringValue(task, "promotion_name", "");
			if (name.Length > 0)
			{
				return name;
			}
			string type = StringValue(task, "promotion_type", "");
			string promotionId = StringValue(task, "promotion_id", "");
			string typeName = PromotionTypeDisplayName(type);
			if (promotionId.Length == 0)
			{
				return typeName;
			}
			return typeName + " " + promotionId;
		}

		private static string BatchActivityDisplayName(JsonElement task)
		{
			return BatchActivityDisplayName(IntValue(task, "promotions_total", 0));
		}

		private static string BatchActivityDisplayName(int count)
		{
			if (count <= 0)
			{
				return "多个活动";
			}
			return $"多个活动（{count}个）";
		}
	}

	private sealed record ComboItem(string Value, string Text)
	{
		public override string ToString()
		{
			return Text;
		}
	}

	private sealed class ActivityChoice
	{
		public string Key { get; }

		public string DisplayName { get; set; }

		public int Count { get; set; }

		public ActivityChoice(string key, string displayName, int count)
		{
			Key = key;
			DisplayName = displayName;
			Count = count;
		}
	}

	private sealed class TaskGridRow
	{
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

		public List<string> SkippedReasonDetails { get; }

		public string StoreScopeText => ScopeText(StoreNames, "多个店铺");

		public string SiteScopeText => ScopeText(SiteNames, "多个站点");

		public string ReasonTooltipText
		{
			get
			{
				List<string> reasons = FailureReasonDetails.Concat(SkippedReasonDetails).Where((string line) => !string.IsNullOrWhiteSpace(line)).Distinct().ToList();
				if (reasons.Count <= 0)
				{
					return ReasonText;
				}
				return string.Join(Environment.NewLine, reasons);
			}
		}

		public TaskGridRow(List<int> taskIds, DateTime createdAt, string timeText, string actionText, string storeText, string siteText, string sellerActivity, string officialActivity, string modeText, string quantityText, int total, int success, int failed, int skipped, string reasonText, string mergeKey, bool isBatch, int activityTotal, string summaryJson, IEnumerable<string> detailLines, IEnumerable<string> failureReasonDetails, IEnumerable<string> skippedReasonDetails)
		{
			TaskIds = taskIds;
			CreatedAt = createdAt;
			TimeText = timeText;
			ActionText = actionText;
			StoreText = storeText;
			SiteText = siteText;
			StoreNames = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
			SiteNames = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
			if (!string.IsNullOrWhiteSpace(storeText))
			{
				StoreNames.Add(storeText);
			}
			if (!string.IsNullOrWhiteSpace(siteText))
			{
				SiteNames.Add(siteText);
			}
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
			DetailLines = detailLines.Where((string line) => !string.IsNullOrWhiteSpace(line)).ToList();
			FailureReasonDetails = failureReasonDetails.Where((string line) => !string.IsNullOrWhiteSpace(line)).ToList();
			SkippedReasonDetails = skippedReasonDetails.Where((string line) => !string.IsNullOrWhiteSpace(line)).ToList();
		}

		private static string ScopeText(IReadOnlyCollection<string> names, string multiLabel)
		{
			string[] clean = (from name in names.Where((string name) => !string.IsNullOrWhiteSpace(name)).Distinct<string>(StringComparer.OrdinalIgnoreCase)
				orderby name
				select name).ToArray();
			if (clean.Length == 0)
			{
				return "";
			}
			if (clean.Length == 1)
			{
				return clean[0];
			}
			return $"{multiLabel}（{clean.Length}个）";
		}
	}

	private sealed record AccountInfo(string AccountId, string DisplayName, string SiteId, string StoreName);

	private sealed record OperatingSiteOption(string AccountId, string StoreName, string SiteId, string SiteName, bool SuggestedOperating, bool Configured, bool Operating, string EvidenceText);

	private sealed class OperatingSitesDialog : Form
	{
		private readonly CheckedListBox _sites = new CheckedListBox();

		public IReadOnlyDictionary<string, List<string>> SelectedOperatingSites { get; private set; } = new Dictionary<string, List<string>>(StringComparer.OrdinalIgnoreCase);

		public OperatingSitesDialog(IReadOnlyList<OperatingSiteOption> options, IReadOnlyDictionary<string, List<string>> current)
		{
			SelectedOperatingSites = current.ToDictionary((KeyValuePair<string, List<string>> pair) => pair.Key, (KeyValuePair<string, List<string>> pair) => pair.Value.ToList(), StringComparer.OrdinalIgnoreCase);
			Text = "经营站点";
			StartPosition = FormStartPosition.CenterParent;
			FormBorderStyle = FormBorderStyle.FixedDialog;
			MaximizeBox = false;
			MinimizeBox = false;
			ClientSize = new Size(650, 600);
			UiTheme.ApplyForm(this);
			Label help = new Label
			{
				Text = "只勾选实际经营的站点。程序已按活跃商品提供建议；暂时没有活动但仍在经营的站点请保留勾选。保存后，未勾选站点不参与活动检测、创建候选和日常批量操作。",
				Left = 18,
				Top = 16,
				Width = 614,
				Height = 58,
				ForeColor = UiTheme.MainText,
				BackColor = Color.Transparent
			};
			_sites.Left = 18;
			_sites.Top = 82;
			_sites.Width = 614;
			_sites.Height = 450;
			_sites.CheckOnClick = true;
			_sites.BackColor = UiTheme.InputBackground;
			_sites.ForeColor = UiTheme.MainText;
			foreach (OperatingSiteOption option in options)
			{
				bool isChecked = current.TryGetValue(option.AccountId, out List<string>? configuredSites)
					? configuredSites.Contains(option.SiteId, StringComparer.OrdinalIgnoreCase)
					: option.SuggestedOperating;
				_sites.Items.Add(new OperatingSiteChoice(option), isChecked);
			}
			Button save = new Button { Text = "保存经营范围", Left = 446, Top = 550, Width = 110, DialogResult = DialogResult.OK };
			Button cancel = new Button { Text = "取消", Left = 568, Top = 550, Width = 64, DialogResult = DialogResult.Cancel };
			save.Click += delegate
			{
				Dictionary<string, List<string>> selected = current.ToDictionary((KeyValuePair<string, List<string>> pair) => pair.Key, (KeyValuePair<string, List<string>> pair) => pair.Value.ToList(), StringComparer.OrdinalIgnoreCase);
				foreach (string accountId in options.Select((OperatingSiteOption option) => option.AccountId).Distinct(StringComparer.OrdinalIgnoreCase))
				{
					selected[accountId] = new List<string>();
				}
				foreach (object item in _sites.CheckedItems)
				{
					if (item is OperatingSiteChoice choice)
					{
						selected[choice.Option.AccountId].Add(choice.Option.SiteId);
					}
				}
				SelectedOperatingSites = selected.ToDictionary((KeyValuePair<string, List<string>> pair) => pair.Key, (KeyValuePair<string, List<string>> pair) => pair.Value.Distinct(StringComparer.OrdinalIgnoreCase).OrderBy((string siteId) => siteId).ToList(), StringComparer.OrdinalIgnoreCase);
			};
			Controls.Add(help);
			Controls.Add(_sites);
			Controls.Add(save);
			Controls.Add(cancel);
			UiTheme.ApplyControlTree(this);
			UiTheme.StylePrimaryButton(save);
			UiTheme.StyleButton(cancel, primary: false);
			AcceptButton = save;
			CancelButton = cancel;
		}

		private sealed class OperatingSiteChoice
		{
			public OperatingSiteOption Option { get; }

			public OperatingSiteChoice(OperatingSiteOption option)
			{
				Option = option;
			}

			public override string ToString()
			{
				return $"{Option.StoreName} / {Option.SiteName}    {Option.EvidenceText}";
			}
		}
	}

	private sealed class RoundedPanel : Panel
	{
		public int CornerRadius { get; set; } = 8;

		public Color FillColor { get; set; } = UiTheme.CardBackground;

		public Color BorderColor { get; set; } = UiTheme.CardBorder;

		public int BorderWidth { get; set; } = 1;

		public RoundedPanel()
		{
			SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer | ControlStyles.UserPaint, value: true);
			BackColor = Color.Transparent;
		}

		protected override void OnPaintBackground(PaintEventArgs e)
		{
			e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
			using GraphicsPath path = RoundedPath(ClientRectangle, CornerRadius);
			using SolidBrush brush = new SolidBrush(FillColor);
			e.Graphics.FillPath(brush, path);
		}

		protected override void OnPaint(PaintEventArgs e)
		{
			base.OnPaint(e);
			e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
			Rectangle bounds = new Rectangle(0, 0, Math.Max(0, Width - 1), Math.Max(0, Height - 1));
			using GraphicsPath path = RoundedPath(bounds, CornerRadius);
			using Pen pen = new Pen(BorderColor, BorderWidth);
			e.Graphics.DrawPath(pen, path);
		}
	}

	private sealed class RoundedButton : Button
	{
		private bool _hovered;

		private bool _pressed;

		public bool Primary { get; set; }

		public bool Selected { get; set; }

		public int CornerRadius { get; set; } = 7;

		public Color BorderColor { get; set; } = UiTheme.GoldBorder;

		public Color HoverColor { get; set; } = UiTheme.HoverBackground;

		public Color PressedColor { get; set; } = UiTheme.SecondaryBackground;

		public RoundedButton()
		{
			SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer | ControlStyles.UserPaint, value: true);
			FlatStyle = FlatStyle.Flat;
			FlatAppearance.BorderSize = 0;
			UseVisualStyleBackColor = false;
			Cursor = Cursors.Hand;
			TabStop = true;
		}

		protected override void OnMouseEnter(EventArgs e)
		{
			_hovered = true;
			Invalidate();
			base.OnMouseEnter(e);
		}

		protected override void OnMouseLeave(EventArgs e)
		{
			_hovered = false;
			_pressed = false;
			Invalidate();
			base.OnMouseLeave(e);
		}

		protected override void OnMouseDown(MouseEventArgs mevent)
		{
			_pressed = true;
			Invalidate();
			base.OnMouseDown(mevent);
		}

		protected override void OnMouseUp(MouseEventArgs mevent)
		{
			_pressed = false;
			Invalidate();
			base.OnMouseUp(mevent);
		}

		protected override void OnPaint(PaintEventArgs pevent)
		{
			pevent.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
			Color fill = _pressed ? PressedColor : (_hovered ? HoverColor : (Selected ? UiTheme.SelectedGreen : BackColor));
			Color border = Focused ? UiTheme.GoldFocus : (Selected ? UiTheme.GreenBorder : BorderColor);
			Rectangle bounds = new Rectangle(0, 0, Math.Max(0, Width - 1), Math.Max(0, Height - 1));
			using GraphicsPath path = RoundedPath(bounds, CornerRadius);
			using SolidBrush brush = new SolidBrush(fill);
			using Pen pen = new Pen(border, 1f);
			pevent.Graphics.FillPath(brush, path);
			pevent.Graphics.DrawPath(pen, path);
			TextRenderer.DrawText(pevent.Graphics, Text, Font, bounds, ForeColor, TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter | TextFormatFlags.EndEllipsis);
		}
	}

	private sealed class DarkComboBox : ComboBox
	{
		private const int WsBorder = 0x00800000;

		private const int WsExClientEdge = 0x00000200;

		private const int WsExStaticEdge = 0x00020000;

		private const int WmPaint = 15;

		private const int WmNcPaint = 133;

		private const int WmPrintClient = 792;

		public DarkComboBox()
		{
			DrawMode = DrawMode.OwnerDrawFixed;
			ItemHeight = 28;
			DropDownStyle = ComboBoxStyle.DropDownList;
			FlatStyle = FlatStyle.Flat;
			BackColor = UiTheme.InputBackground;
			ForeColor = UiTheme.MainText;
		}

		protected override CreateParams CreateParams
		{
			get
			{
				CreateParams parameters = base.CreateParams;
				parameters.Style &= ~WsBorder;
				parameters.ExStyle &= ~(WsExClientEdge | WsExStaticEdge);
				return parameters;
			}
		}

		protected override void WndProc(ref Message m)
		{
			if (m.Msg == WmNcPaint)
			{
				return;
			}
			base.WndProc(ref m);
			if (m.Msg == WmPaint || m.Msg == WmPrintClient)
			{
				PaintDropDownButton();
			}
		}

		private void PaintDropDownButton()
		{
			if (!IsHandleCreated || Width <= 0 || Height <= 0)
			{
				return;
			}
			int buttonWidth = Math.Max(24, SystemInformation.VerticalScrollBarWidth + 4);
			Rectangle buttonBounds = new Rectangle(Math.Max(0, Width - buttonWidth), 0, buttonWidth, Height);
			using Graphics graphics = CreateGraphics();
			graphics.SmoothingMode = SmoothingMode.AntiAlias;
			using SolidBrush background = new SolidBrush(UiTheme.InputBackground);
			int edgeThickness = Math.Min(2, Math.Max(1, Height / 10));
			graphics.FillRectangle(background, new Rectangle(0, 0, Width, edgeThickness));
			graphics.FillRectangle(background, new Rectangle(0, Math.Max(0, Height - edgeThickness), Width, edgeThickness));
			graphics.FillRectangle(background, new Rectangle(0, 0, edgeThickness, Height));
			graphics.FillRectangle(background, new Rectangle(Math.Max(0, Width - edgeThickness), 0, edgeThickness, Height));
			graphics.FillRectangle(background, buttonBounds);
			using Pen separator = new Pen(Focused ? UiTheme.GoldFocus : UiTheme.GoldBorder, 1f);
			graphics.DrawLine(separator, buttonBounds.Left, 5, buttonBounds.Left, Math.Max(5, Height - 6));
			int centerX = buttonBounds.Left + buttonBounds.Width / 2;
			int centerY = Height / 2;
			using Pen arrow = new Pen(Enabled ? UiTheme.MutedText : UiTheme.WeakText, 1.6f)
			{
				StartCap = LineCap.Round,
				EndCap = LineCap.Round
			};
			graphics.DrawLine(arrow, centerX - 4, centerY - 2, centerX, centerY + 2);
			graphics.DrawLine(arrow, centerX, centerY + 2, centerX + 4, centerY - 2);
		}

		protected override void OnDrawItem(DrawItemEventArgs e)
		{
			if (e.Index < 0)
			{
				return;
			}
			bool selected = (e.State & DrawItemState.Selected) == DrawItemState.Selected;
			using SolidBrush background = new SolidBrush(selected ? UiTheme.PrimaryGreen : UiTheme.CardBackground);
			e.Graphics.FillRectangle(background, e.Bounds);
			string text = GetItemText(Items[e.Index]) ?? "";
			TextRenderer.DrawText(e.Graphics, text, Font, e.Bounds, selected ? UiTheme.ButtonText : UiTheme.MainText, TextFormatFlags.Left | TextFormatFlags.VerticalCenter | TextFormatFlags.EndEllipsis | TextFormatFlags.NoPrefix);
			e.DrawFocusRectangle();
		}
	}

	private sealed class DarkNumericUpDown : NumericUpDown
	{
		private readonly DarkSpinButtons _spinButtons;

		public DarkNumericUpDown()
		{
			BorderStyle = BorderStyle.None;
			BackColor = UiTheme.InputBackground;
			ForeColor = UiTheme.MainText;
			_spinButtons = new DarkSpinButtons(this)
			{
				Dock = DockStyle.Right,
				Width = 24,
				AccessibleName = "增加或减少数值",
				TabStop = false
			};
		}

		protected override void OnHandleCreated(EventArgs e)
		{
			base.OnHandleCreated(e);
			Control? nativeButtons = Controls.Cast<Control>().FirstOrDefault(control => control != _spinButtons && control.GetType().Name.Contains("UpDownButtons", StringComparison.Ordinal));
			if (nativeButtons != null)
			{
				nativeButtons.Visible = false;
			}
			if (!Controls.Contains(_spinButtons))
			{
				Controls.Add(_spinButtons);
			}
			_spinButtons.BringToFront();
		}

		protected override void OnEnabledChanged(EventArgs e)
		{
			base.OnEnabledChanged(e);
			_spinButtons.Enabled = Enabled;
			_spinButtons.Invalidate();
		}

		protected override void OnMouseWheel(MouseEventArgs e)
		{
			base.OnMouseWheel(e);
		}

		private sealed class DarkSpinButtons : Control
		{
			private readonly DarkNumericUpDown _owner;

			private int _hoverHalf = -1;

			public DarkSpinButtons(DarkNumericUpDown owner)
			{
				_owner = owner;
				SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer | ControlStyles.UserPaint, value: true);
				BackColor = UiTheme.InputBackground;
				Cursor = Cursors.Hand;
			}

			protected override void OnMouseMove(MouseEventArgs e)
			{
				int hoverHalf = e.Y < Height / 2 ? 0 : 1;
				if (_hoverHalf != hoverHalf)
				{
					_hoverHalf = hoverHalf;
					Invalidate();
				}
				base.OnMouseMove(e);
			}

			protected override void OnMouseLeave(EventArgs e)
			{
				_hoverHalf = -1;
				Invalidate();
				base.OnMouseLeave(e);
			}

			protected override void OnMouseDown(MouseEventArgs e)
			{
				if (e.Button == MouseButtons.Left && Enabled)
				{
					_owner.Focus();
					if (e.Y < Height / 2)
					{
						_owner.UpButton();
					}
					else
					{
						_owner.DownButton();
					}
				}
				base.OnMouseDown(e);
			}

			protected override void OnPaint(PaintEventArgs e)
			{
				e.Graphics.Clear(UiTheme.InputBackground);
				int half = Height / 2;
				if (_hoverHalf >= 0 && Enabled)
				{
					Rectangle hoverBounds = _hoverHalf == 0 ? new Rectangle(0, 0, Width, half) : new Rectangle(0, half, Width, Height - half);
					using SolidBrush hover = new SolidBrush(UiTheme.HoverBackground);
					e.Graphics.FillRectangle(hover, hoverBounds);
				}
				using Pen divider = new Pen(_owner.Focused ? UiTheme.GoldFocus : UiTheme.GoldBorder, 1f);
				e.Graphics.DrawLine(divider, 0, 3, 0, Math.Max(3, Height - 4));
				e.Graphics.DrawLine(divider, 4, half, Math.Max(4, Width - 5), half);
				Color arrowColor = Enabled ? UiTheme.MutedText : UiTheme.WeakText;
				using SolidBrush arrow = new SolidBrush(arrowColor);
				int centerX = Width / 2;
				int upperY = Math.Max(4, half / 2);
				int lowerY = half + Math.Max(4, (Height - half) / 2);
				e.Graphics.FillPolygon(arrow, new Point[3] { new Point(centerX, upperY - 2), new Point(centerX - 4, upperY + 2), new Point(centerX + 4, upperY + 2) });
				e.Graphics.FillPolygon(arrow, new Point[3] { new Point(centerX - 4, lowerY - 2), new Point(centerX + 4, lowerY - 2), new Point(centerX, lowerY + 2) });
			}
		}
	}

	private sealed class DarkMenuColorTable : ProfessionalColorTable
	{
		public override Color ToolStripDropDownBackground => UiTheme.CardBackground;

		public override Color MenuItemSelected => UiTheme.PrimaryGreen;

		public override Color MenuItemBorder => UiTheme.GreenBorder;

		public override Color ImageMarginGradientBegin => UiTheme.CardBackground;

		public override Color ImageMarginGradientMiddle => UiTheme.CardBackground;

		public override Color ImageMarginGradientEnd => UiTheme.CardBackground;

		public override Color SeparatorDark => UiTheme.NormalBorder;

		public override Color SeparatorLight => UiTheme.CardBorder;
	}

	private static GraphicsPath RoundedPath(Rectangle bounds, int radius)
	{
		GraphicsPath path = new GraphicsPath();
		if (bounds.Width <= 0 || bounds.Height <= 0)
		{
			return path;
		}
		int diameter = Math.Max(2, Math.Min(radius * 2, Math.Min(bounds.Width, bounds.Height)));
		Rectangle arc = new Rectangle(bounds.Location, new Size(diameter, diameter));
		path.AddArc(arc, 180f, 90f);
		arc.X = bounds.Right - diameter;
		path.AddArc(arc, 270f, 90f);
		arc.Y = bounds.Bottom - diameter;
		path.AddArc(arc, 0f, 90f);
		arc.X = bounds.Left;
		path.AddArc(arc, 90f, 90f);
		path.CloseFigure();
		return path;
	}

	[DllImport("dwmapi.dll")]
	private static extern int DwmSetWindowAttribute(IntPtr hwnd, int attribute, ref int value, int valueSize);

	[DllImport("uxtheme.dll", CharSet = CharSet.Unicode)]
	private static extern int SetWindowTheme(IntPtr hwnd, string? subAppName, string? subIdList);

	private static void ApplyDarkTitleBar(IntPtr handle)
	{
		if (!OperatingSystem.IsWindows())
		{
			return;
		}
		try
		{
			int enabled = 1;
			DwmSetWindowAttribute(handle, 20, ref enabled, sizeof(int));
			int caption = ColorRef(UiTheme.MainBackground);
			DwmSetWindowAttribute(handle, 35, ref caption, sizeof(int));
			int text = ColorRef(UiTheme.MainText);
			DwmSetWindowAttribute(handle, 36, ref text, sizeof(int));
			int border = ColorRef(UiTheme.NormalBorder);
			DwmSetWindowAttribute(handle, 34, ref border, sizeof(int));
		}
		catch
		{
		}
	}

	private static int ColorRef(Color color)
	{
		return color.R | (color.G << 8) | (color.B << 16);
	}

	private static void ApplyNativeDarkMode(Control root)
	{
		try
		{
			SetWindowTheme(root.Handle, "DarkMode_Explorer", null);
		}
		catch
		{
		}
		foreach (Control child in root.Controls)
		{
			ApplyNativeDarkMode(child);
		}
	}

	private sealed class ApiJson : IDisposable
	{
		private readonly JsonDocument _document;

		public int StatusCode { get; }

		public JsonElement Root => _document.RootElement;

		public ApiJson(int statusCode, JsonDocument document)
		{
			StatusCode = statusCode;
			_document = document;
		}

		public void Dispose()
		{
			_document.Dispose();
		}
	}

	private sealed class SellerCampaignTarget
	{
		public string AccountId { get; }

		public string SiteId { get; }

		public string StoreName { get; }

		public string SiteName { get; }

		public string Label => StoreName + " / " + SiteName;

		public SellerCampaignTarget(string accountId, string siteId, string storeName, string siteName)
		{
			AccountId = accountId;
			SiteId = siteId;
			StoreName = string.IsNullOrWhiteSpace(storeName) ? "当前店铺" : storeName;
			SiteName = string.IsNullOrWhiteSpace(siteName) ? siteId : siteName;
		}

		public override string ToString()
		{
			return Label;
		}
	}

	private sealed class SellerCampaignCreateDialog : Form
	{
		private readonly TextBox _nameBox = new TextBox();

		private readonly DateTimePicker _startPicker = new DateTimePicker();

		private readonly DateTimePicker _finishPicker = new DateTimePicker();

		private readonly CheckedListBox _scopeList = new CheckedListBox();

		public string ActivityName => _nameBox.Text.Trim();

		public DateTime StartDate => _startPicker.Value.Date;

		public DateTime FinishDate => _finishPicker.Value.Date;

		public DateTime ApiFinishDate => FinishDate.AddDays(1.0);

		public IReadOnlyList<SellerCampaignTarget> SelectedTargets
		{
			get
			{
				return _scopeList.CheckedItems.Cast<SellerCampaignTarget>().ToList();
			}
		}

		private static DateTime EndOfMonth(DateTime date)
		{
			return new DateTime(date.Year, date.Month, DateTime.DaysInMonth(date.Year, date.Month));
		}

		private void SyncFinishPickerRange()
		{
			DateTime start = StartDate;
			DateTime max = EndOfMonth(start);
			if (_finishPicker.MaxDate < max)
			{
				_finishPicker.MaxDate = max;
			}
			if (_finishPicker.MinDate > start)
			{
				_finishPicker.MinDate = start;
			}
			DateTime value = FinishDate;
			if (value < start)
			{
				value = start;
			}
			if (value > max)
			{
				value = max;
			}
			_finishPicker.Value = value;
			_finishPicker.MinDate = start;
			_finishPicker.MaxDate = max;
		}

		public SellerCampaignCreateDialog(IReadOnlyList<SellerCampaignTarget> missingScopes)
		{
			Text = "创建自建活动";
			base.Width = 560;
			base.Height = 430;
			base.StartPosition = FormStartPosition.CenterParent;
			base.FormBorderStyle = FormBorderStyle.FixedDialog;
			base.MaximizeBox = false;
			base.MinimizeBox = false;
			Font = new Font("Microsoft YaHei", 10f);
			BackColor = UiTheme.MainBackground;
			ForeColor = UiTheme.MainText;
			Label title = new Label
			{
				Text = "接口未读取到自建活动不代表后台没有。请只勾选已在网页后台核对确实没有自建活动的站点。",
				Left = 18,
				Top = 18,
				Width = 505,
				Height = 46,
				ForeColor = UiTheme.MainText
			};
			_scopeList.Left = 18;
			_scopeList.Top = 70;
			_scopeList.Width = 505;
			_scopeList.Height = 95;
			_scopeList.CheckOnClick = true;
			_scopeList.BackColor = UiTheme.InputBackground;
			_scopeList.ForeColor = UiTheme.MainText;
			_scopeList.BorderStyle = BorderStyle.FixedSingle;
			foreach (SellerCampaignTarget target in missingScopes)
			{
				_scopeList.Items.Add(target, isChecked: false);
			}
			Label nameLabel = new Label
			{
				Text = "自建活动名",
				Left = 18,
				Top = 184,
				Width = 120,
				Height = 26
			};
			_nameBox.Left = 140;
			_nameBox.Top = 180;
			_nameBox.Width = 383;
			_nameBox.Height = 32;
			_nameBox.BackColor = UiTheme.InputBackground;
			_nameBox.ForeColor = UiTheme.MainText;
			_nameBox.BorderStyle = BorderStyle.FixedSingle;
			_nameBox.Text = "95";
			Label startLabel = new Label
			{
				Text = "开始日期",
				Left = 18,
				Top = 232,
				Width = 120,
				Height = 26
			};
			_startPicker.Left = 140;
			_startPicker.Top = 228;
			_startPicker.Width = 180;
			_startPicker.Format = DateTimePickerFormat.Custom;
			_startPicker.CustomFormat = "yyyy-MM-dd";
			_startPicker.Value = DateTime.Today;
			_startPicker.ValueChanged += delegate
			{
				SyncFinishPickerRange();
			};
			Label finishLabel = new Label
			{
				Text = "结束日期",
				Left = 18,
				Top = 280,
				Width = 120,
				Height = 26
			};
			_finishPicker.Left = 140;
			_finishPicker.Top = 276;
			_finishPicker.Width = 180;
			_finishPicker.Format = DateTimePickerFormat.Custom;
			_finishPicker.CustomFormat = "yyyy-MM-dd";
			_finishPicker.Value = EndOfMonth(DateTime.Today);
			SyncFinishPickerRange();
			Label note = new Label
			{
				Text = "本窗口只填写自建活动信息；下一步会让你确认最终创建清单。",
				Left = 18,
				Top = 320,
				Width = 505,
				Height = 28,
				ForeColor = UiTheme.MutedText
			};
			note.Text = "默认不创建；只有勾选并二次确认后才创建自建活动，不创建官方活动。";
			Button okButton = new Button
			{
				Text = "下一步",
				DialogResult = DialogResult.OK,
				Left = 322,
				Top = 360,
				Width = 96,
				Height = 34
			};
			Button cancelButton = new Button
			{
				Text = "取消",
				DialogResult = DialogResult.Cancel,
				Left = 428,
				Top = 360,
				Width = 96,
				Height = 34
			};
			okButton.Click += delegate
			{
				if (ActivityName.Length == 0)
				{
					MessageBox.Show("请填写自建活动名。", "创建自建活动", MessageBoxButtons.OK, MessageBoxIcon.Asterisk);
					base.DialogResult = DialogResult.None;
				}
				else if (SelectedTargets.Count == 0)
				{
					MessageBox.Show("请至少勾选一个要创建自建活动的店铺站点。", "创建自建活动", MessageBoxButtons.OK, MessageBoxIcon.Asterisk);
					base.DialogResult = DialogResult.None;
				}
				else if (FinishDate < StartDate)
				{
					MessageBox.Show("结束日期不能早于开始日期。", "创建自建活动", MessageBoxButtons.OK, MessageBoxIcon.Asterisk);
					base.DialogResult = DialogResult.None;
				}
				else if (FinishDate > EndOfMonth(StartDate))
				{
					MessageBox.Show("结束日期不能超过开始日期所在月份的最后一天。", "创建自建活动", MessageBoxButtons.OK, MessageBoxIcon.Asterisk);
					base.DialogResult = DialogResult.None;
				}
			};
			base.AcceptButton = okButton;
			base.CancelButton = cancelButton;
			base.Controls.AddRange(new Control[11]
			{
				title, _scopeList, nameLabel, _nameBox, startLabel, _startPicker, finishLabel, _finishPicker, note, okButton,
				cancelButton
			});
		}
	}

	private sealed class TextDetailForm : Form
	{
		public TextDetailForm(string title, string text)
		{
			string text2 = text;
			TextDetailForm textDetailForm = this;
			Text = title;
			base.Width = 760;
			base.Height = 520;
			base.StartPosition = FormStartPosition.CenterParent;
			Font = new Font("Microsoft YaHei", 10f);
			BackColor = UiTheme.MainBackground;
			ForeColor = UiTheme.MainText;
			TextBox box = new TextBox
			{
				Multiline = true,
				ReadOnly = true,
				ScrollBars = ScrollBars.Both,
				Dock = DockStyle.Fill,
				WordWrap = false,
				Text = text2,
				BackColor = UiTheme.TableBackground,
				ForeColor = UiTheme.MainText,
				BorderStyle = BorderStyle.FixedSingle,
				Font = new Font("Microsoft YaHei", 10f)
			};
			base.Controls.Add(box);
			FlowLayoutPanel bottom = new FlowLayoutPanel
			{
				Dock = DockStyle.Bottom,
				Height = 42,
				FlowDirection = FlowDirection.RightToLeft,
				Padding = new Padding(8),
				BackColor = UiTheme.MainBackground
			};
			Button close = new Button
			{
				Text = "关闭",
				Width = 82,
				Height = 26
			};
			UiTheme.StyleButton(close, primary: false);
			close.Click += delegate
			{
				textDetailForm.Close();
			};
			Button copy = new Button
			{
				Text = "复制",
				Width = 82,
				Height = 26
			};
			UiTheme.StyleButton(copy, primary: true);
			copy.Click += delegate
			{
				Clipboard.SetText(text2);
			};
			bottom.Controls.Add(close);
			bottom.Controls.Add(copy);
			base.Controls.Add(bottom);
		}
	}

	private sealed class StyledConfirmDialog : Form
	{
		private const int PreferredDialogWidth = 620;

		private const int MinimumDialogWidth = 420;

		private const int DialogScreenMargin = 48;

		private const int ContentHorizontalPadding = 36;

		private const int ContentVerticalPadding = 28;

		private const int TitleAreaHeight = 34;

		private const int ButtonAreaHeight = 54;

		private const int MinimumBodyHeight = 64;

		private readonly Label _body;

		private readonly Panel _bodyViewport;

		private readonly RowStyle _bodyRow;

		public StyledConfirmDialog(string titleText, string message, string okText, string cancelText)
		{
			Text = titleText;
			base.StartPosition = FormStartPosition.CenterParent;
			base.FormBorderStyle = FormBorderStyle.FixedDialog;
			base.MaximizeBox = false;
			base.MinimizeBox = false;
			base.ShowInTaskbar = false;
			UiTheme.ApplyForm(this);
			Label title = new Label
			{
				Text = titleText,
				Dock = DockStyle.Fill,
				TextAlign = ContentAlignment.MiddleLeft,
				Margin = new Padding(0),
				Font = new Font("Microsoft YaHei UI", 10f, FontStyle.Bold),
				ForeColor = UiTheme.MainText,
				BackColor = Color.Transparent
			};
			_body = new Label
			{
				Text = message,
				AutoSize = true,
				Location = Point.Empty,
				Margin = new Padding(0),
				ForeColor = UiTheme.MainText,
				BackColor = Color.Transparent,
				Font = new Font("Microsoft YaHei UI", 9.5f, FontStyle.Regular)
			};
			_bodyViewport = new Panel
			{
				Dock = DockStyle.Fill,
				Margin = new Padding(0),
				AutoScroll = true,
				BackColor = Color.Transparent
			};
			_bodyViewport.Controls.Add(_body);
			Button ok = new Button
			{
				Text = okText,
				Width = 92,
				Height = 32,
				Margin = new Padding(8, 8, 0, 8),
				DialogResult = DialogResult.OK
			};
			Button cancel = new Button
			{
				Text = cancelText,
				Width = 92,
				Height = 32,
				Margin = new Padding(8, 8, 0, 8),
				DialogResult = DialogResult.Cancel
			};
			FlowLayoutPanel actions = new FlowLayoutPanel
			{
				Dock = DockStyle.Bottom,
				FlowDirection = FlowDirection.RightToLeft,
				WrapContents = false,
				Margin = new Padding(0),
				Padding = new Padding(0),
				BackColor = Color.Transparent
			};
			actions.Controls.Add(cancel);
			actions.Controls.Add(ok);
			TableLayoutPanel layout = new TableLayoutPanel
			{
				Dock = DockStyle.Fill,
				ColumnCount = 1,
				RowCount = 3,
				Padding = new Padding(18, 14, 18, 14),
				Margin = new Padding(0),
				BackColor = Color.Transparent
			};
			layout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100f));
			layout.RowStyles.Add(new RowStyle(SizeType.Absolute, TitleAreaHeight));
			_bodyRow = new RowStyle(SizeType.Absolute, MinimumBodyHeight);
			layout.RowStyles.Add(_bodyRow);
			layout.RowStyles.Add(new RowStyle(SizeType.Absolute, ButtonAreaHeight));
			layout.Controls.Add(title, 0, 0);
			layout.Controls.Add(_bodyViewport, 0, 1);
			layout.Controls.Add(actions, 0, 2);
			base.Controls.Add(layout);
			UiTheme.ApplyControlTree(this);
			UiTheme.StylePrimaryButton(ok);
			UiTheme.StyleButton(cancel, primary: false);
			base.AcceptButton = ok;
			base.CancelButton = cancel;
			ApplyMeasuredLayout(SystemInformation.WorkingArea);
		}

		protected override void OnLoad(EventArgs e)
		{
			Control screenReference = base.Owner ?? this;
			ApplyMeasuredLayout(Screen.FromControl(screenReference).WorkingArea);
			base.OnLoad(e);
		}

		private void ApplyMeasuredLayout(Rectangle workingArea)
		{
			int availableWidth = Math.Max(320, workingArea.Width - DialogScreenMargin);
			int dialogWidth = availableWidth >= MinimumDialogWidth ? Math.Min(PreferredDialogWidth, availableWidth) : availableWidth;
			int bodyTextWidth = Math.Max(280, dialogWidth - ContentHorizontalPadding - SystemInformation.VerticalScrollBarWidth);
			TextFormatFlags measureFlags = TextFormatFlags.WordBreak | TextFormatFlags.TextBoxControl | TextFormatFlags.NoPadding;
			Size measuredBody = TextRenderer.MeasureText(_body.Text, _body.Font, new Size(bodyTextWidth, int.MaxValue), measureFlags);
			int maximumClientHeight = Math.Max(220, workingArea.Height - DialogScreenMargin);
			int maximumBodyHeight = Math.Max(MinimumBodyHeight, maximumClientHeight - TitleAreaHeight - ButtonAreaHeight - ContentVerticalPadding);
			int desiredBodyHeight = Math.Max(MinimumBodyHeight, measuredBody.Height + 10);
			int bodyHeight = Math.Min(desiredBodyHeight, maximumBodyHeight);
			_body.MaximumSize = new Size(bodyTextWidth, 0);
			_body.MinimumSize = new Size(bodyTextWidth, 0);
			_bodyRow.Height = bodyHeight;
			_bodyViewport.AutoScrollMinSize = new Size(bodyTextWidth, measuredBody.Height + 4);
			base.ClientSize = new Size(dialogWidth, TitleAreaHeight + bodyHeight + ButtonAreaHeight + ContentVerticalPadding);
		}
	}

	private sealed class OAuthCallbackDialog : Form
	{
		private readonly TextBox _input = new TextBox();

		public string CallbackText => _input.Text.Trim();

		public OAuthCallbackDialog()
		{
			Text = "粘贴授权结果";
			base.StartPosition = FormStartPosition.CenterParent;
			base.FormBorderStyle = FormBorderStyle.FixedDialog;
			base.MaximizeBox = false;
			base.MinimizeBox = false;
			base.ClientSize = new Size(520, 170);
			UiTheme.ApplyForm(this);
			Label label = new Label
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
			Button ok = new Button
			{
				Text = "完成授权",
				Left = 320,
				Width = 88,
				Top = 122,
				DialogResult = DialogResult.OK
			};
			Button cancel = new Button
			{
				Text = "取消",
				Left = 424,
				Width = 80,
				Top = 122,
				DialogResult = DialogResult.Cancel
			};
			base.Controls.Add(label);
			base.Controls.Add(_input);
			base.Controls.Add(ok);
			base.Controls.Add(cancel);
			UiTheme.ApplyControlTree(this);
			UiTheme.StylePrimaryButton(ok);
			UiTheme.StyleButton(cancel, primary: false);
			base.AcceptButton = ok;
			base.CancelButton = cancel;
		}
	}

	private sealed class AuthorizationLinkDialog : Form
	{
		public AuthorizationLinkDialog(string url)
		{
			Text = "复制授权链接";
			base.StartPosition = FormStartPosition.CenterParent;
			base.FormBorderStyle = FormBorderStyle.FixedDialog;
			base.MaximizeBox = false;
			base.MinimizeBox = false;
			base.ClientSize = new Size(560, 210);
			UiTheme.ApplyForm(this);
			Label label = new Label
			{
				Text = "剪贴板复制失败。请手动复制下面的授权链接，到目标账号已登录的浏览器中打开：",
				Left = 16,
				Top = 16,
				Width = 528,
				Height = 34,
				ForeColor = UiTheme.MainText,
				BackColor = Color.Transparent
			};
			TextBox input = new TextBox
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
			Button ok = new Button
			{
				Text = "关闭",
				Left = 464,
				Width = 80,
				Top = 162,
				DialogResult = DialogResult.OK
			};
			base.Controls.Add(label);
			base.Controls.Add(input);
			base.Controls.Add(ok);
			UiTheme.ApplyControlTree(this);
			UiTheme.StylePrimaryButton(ok);
			base.AcceptButton = ok;
		}
	}

	private sealed class ConfirmationPackageForm : Form
	{
		private readonly TextBox _summary = new TextBox();

		public ConfirmationPackageForm(JsonElement package)
		{
			Text = "执行确认信息";
			base.StartPosition = FormStartPosition.CenterParent;
			base.MinimizeBox = false;
			base.MaximizeBox = false;
			base.ClientSize = new Size(720, 460);
			UiTheme.ApplyForm(this);
			Label title = new Label
			{
				Text = "执行确认信息",
				Left = 16,
				Top = 14,
				Width = 680,
				Height = 24,
				Font = new Font("Microsoft YaHei UI", 10f, FontStyle.Bold),
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
			Button copy = new Button
			{
				Text = "复制摘要",
				Left = 520,
				Width = 88,
				Top = 414
			};
			copy.Click += delegate
			{
				Clipboard.SetText(_summary.Text);
			};
			Button close = new Button
			{
				Text = "关闭",
				Left = 624,
				Width = 80,
				Top = 414,
				DialogResult = DialogResult.OK
			};
			base.Controls.Add(title);
			base.Controls.Add(_summary);
			base.Controls.Add(copy);
			base.Controls.Add(close);
			UiTheme.ApplyControlTree(this);
			UiTheme.StyleButton(copy, primary: false);
			UiTheme.StylePrimaryButton(close);
			base.AcceptButton = close;
			base.CancelButton = close;
		}

		private static string BuildSummary(JsonElement package)
		{
			List<string> lines = new List<string>
			{
				"状态：" + StringValue(package, "status", "-"),
				"动作：" + LegacyActionText(StringValue(package, "action", "-")),
				"模式：" + StringValue(package, "mode", "-"),
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
				foreach (JsonElement item in reasons.EnumerateArray())
				{
					lines.Add("- " + item.GetString());
				}
				lines.Add("");
			}
			if (package.TryGetProperty("risk_prompts", out var risks) && risks.ValueKind == JsonValueKind.Array)
			{
				lines.Add("风险提示：");
				foreach (JsonElement item2 in risks.EnumerateArray())
				{
					lines.Add("- " + item2.GetString());
				}
			}
			return string.Join(Environment.NewLine, lines);
		}

		private static string StringValue(JsonElement element, string name, string fallback)
		{
			if (!element.TryGetProperty(name, out var value) || value.ValueKind == JsonValueKind.Null)
			{
				return fallback;
			}
			return value.ToString();
		}

		private static int IntValue(JsonElement element, string name, int fallback)
		{
			if (!element.TryGetProperty(name, out var value))
			{
				return fallback;
			}
			if (value.ValueKind == JsonValueKind.Number && value.TryGetInt32(out var number))
			{
				return number;
			}
			if (!int.TryParse(value.ToString(), out var parsed))
			{
				return fallback;
			}
			return parsed;
		}

		private static string LegacyActionText(string action)
		{
			return action switch
			{
				"enroll" => "批量报活动", 
				"update" => "批量更新", 
				"cancel" => "批量取消", 
				_ => string.IsNullOrWhiteSpace(action) ? "-" : action, 
			};
		}
	}

	private sealed class SettingsDialog : Form
	{
		private readonly TextBox _authDir = new TextBox();

		private readonly TextBox _outputDir = new TextBox();

		private readonly NumericUpDown _sellerDiscount = new NumericUpDown();

		private readonly NumericUpDown _officialDiscount = new NumericUpDown();

		private readonly NumericUpDown _readConcurrency = new NumericUpDown();

		private readonly NumericUpDown _previewConcurrency = new NumericUpDown();

		private readonly NumericUpDown _writeConcurrency = new NumericUpDown();

		private readonly ComboBox _aliasAccountSelect = new ComboBox();

		private readonly TextBox _aliasName = new TextBox();

		private readonly Label _aliasStatus = new Label();

		private readonly TextBox _authSummary = new TextBox();

		private readonly Label _authStatus = new Label();

		private readonly TextBox _callbackInput = new TextBox();

		private Dictionary<string, string> _currentAliases = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);

		private Dictionary<string, List<string>> _operatingSites = new Dictionary<string, List<string>>(StringComparer.OrdinalIgnoreCase);

		private readonly List<OperatingSiteOption> _operatingSiteOptions;

		public Func<Task>? AuthorizeRequestedAsync { get; set; }

		public Func<Task>? CompleteAuthorizationRequestedAsync { get; set; }

		public Func<Task>? RefreshAccountsRequestedAsync { get; set; }

		public Func<Task>? SaveStoreAliasRequestedAsync { get; set; }

		public string CallbackText => _callbackInput.Text.Trim();

		public string SelectedAliasAccountId
		{
			get
			{
				if (!(_aliasAccountSelect.SelectedItem is ComboItem item))
				{
					return "";
				}
				return item.Value;
			}
		}

		public string StoreAliasText => _aliasName.Text.Trim();

		public string AuthDir => _authDir.Text.Trim();

		public string OutputDir => _outputDir.Text.Trim();

		public decimal SellerDiscount => _sellerDiscount.Value;

		public decimal OfficialDiscount => _officialDiscount.Value;

		public decimal ReadConcurrency => _readConcurrency.Value;

		public decimal PreviewConcurrency => _previewConcurrency.Value;

		public decimal WriteConcurrency => _writeConcurrency.Value;

		public IReadOnlyDictionary<string, List<string>> OperatingSites => _operatingSites;

		public SettingsDialog(string authDir, string outputDir, decimal sellerDiscount, decimal officialDiscount, decimal readConcurrency, decimal previewConcurrency, decimal writeConcurrency, string storeSummary, string accountSummary, IReadOnlyList<AccountInfo> accounts, IReadOnlyDictionary<string, string> storeAliases, IReadOnlyDictionary<string, List<string>> operatingSites, IReadOnlyList<OperatingSiteOption> operatingSiteOptions, string concurrencyBenchmarkSummary)
		{
			_operatingSites = operatingSites.ToDictionary((KeyValuePair<string, List<string>> pair) => pair.Key, (KeyValuePair<string, List<string>> pair) => pair.Value.ToList(), StringComparer.OrdinalIgnoreCase);
			_operatingSiteOptions = operatingSiteOptions.ToList();
			Text = "设置";
			base.StartPosition = FormStartPosition.CenterParent;
			base.FormBorderStyle = FormBorderStyle.FixedDialog;
			base.MaximizeBox = false;
			base.MinimizeBox = false;
			base.ClientSize = new Size(680, 760);
			UiTheme.ApplyForm(this);
			int y = 18;
			AddRow("授权目录", _authDir, authDir, ref y);
			_outputDir.Text = outputDir;
			AddNumberRow("自建默认折扣 %", _sellerDiscount, sellerDiscount, 1m, 90m, ref y);
			AddNumberRow("官方默认折扣 %", _officialDiscount, officialDiscount, 1m, 90m, ref y);
			ConfigureHiddenNumber(_readConcurrency, readConcurrency, 1m, 20m);
			ConfigureHiddenNumber(_previewConcurrency, previewConcurrency, 1m, 20m);
			ConfigureHiddenNumber(_writeConcurrency, writeConcurrency, 1m, 700m);
			Label note = new Label
			{
				Text = "自动并发策略（推荐）：报名、更新、取消按实测结果和接口反馈调整。日常只需要维护授权、店铺名称和默认折扣。",
				Left = 16,
				Top = y + 4,
				Width = 640,
				Height = 42,
				ForeColor = UiTheme.MutedText,
				BackColor = Color.Transparent
			};
			base.Controls.Add(note);
			y += 50;
			Label benchmarkNote = new Label
			{
				Text = concurrencyBenchmarkSummary,
				Left = 16,
				Top = y,
				Width = 640,
				Height = 44,
				ForeColor = UiTheme.MutedText,
				BackColor = Color.Transparent
			};
			base.Controls.Add(benchmarkNote);
			y += 50;
			Button advancedToggle = new Button
			{
				Text = "高级设置",
				Left = 16,
				Top = y,
				Width = 92
			};
			base.Controls.Add(advancedToggle);
			Label advancedHint = new Label
			{
				Text = "仅排障、压测或主管要求时调整；普通使用无需修改。",
				Left = 120,
				Top = y + 5,
				Width = 520,
				Height = 24,
				ForeColor = UiTheme.MutedText,
				BackColor = Color.Transparent
			};
			base.Controls.Add(advancedHint);
			y += 42;
			GroupBox aliasGroup = new GroupBox
			{
				Text = "店铺名称",
				Left = 16,
				Top = y,
				Width = 640,
				Height = 105,
				BackColor = UiTheme.CardBackground,
				ForeColor = UiTheme.MainText
			};
			aliasGroup.Controls.Add(new Label
			{
				Text = "账号",
				Left = 14,
				Top = 28,
				Width = 60,
				ForeColor = UiTheme.MainText,
				BackColor = Color.Transparent
			});
			_aliasAccountSelect.Left = 78;
			_aliasAccountSelect.Top = 24;
			_aliasAccountSelect.Width = 210;
			_aliasAccountSelect.DropDownStyle = ComboBoxStyle.DropDownList;
			_aliasAccountSelect.SelectedIndexChanged += delegate
			{
				FillAliasNameFromSelection();
			};
			aliasGroup.Controls.Add(_aliasAccountSelect);
			aliasGroup.Controls.Add(new Label
			{
				Text = "店铺名",
				Left = 306,
				Top = 28,
				Width = 60,
				ForeColor = UiTheme.MainText,
				BackColor = Color.Transparent
			});
			_aliasName.Left = 370;
			_aliasName.Top = 24;
			_aliasName.Width = 150;
			aliasGroup.Controls.Add(_aliasName);
			Button saveAlias = new Button
			{
				Text = "保存店铺名",
				Left = 532,
				Top = 23,
				Width = 92
			};
			saveAlias.Click += async delegate
			{
				await RunDialogActionAsync(SaveStoreAliasRequestedAsync);
			};
			aliasGroup.Controls.Add(saveAlias);
			_aliasStatus.Left = 14;
			_aliasStatus.Top = 64;
			_aliasStatus.Width = 465;
			_aliasStatus.Height = 28;
			_aliasStatus.ForeColor = UiTheme.MutedText;
			_aliasStatus.BackColor = Color.Transparent;
			_aliasStatus.Text = "店铺名只保存在本机，用于主界面显示，不会修改 Mercado 账号。";
			aliasGroup.Controls.Add(_aliasStatus);
			Button operatingSitesButton = new Button
			{
				Text = "设置经营站点",
				Left = 492,
				Top = 61,
				Width = 132
			};
			operatingSitesButton.Click += delegate
			{
				using OperatingSitesDialog dialog = new OperatingSitesDialog(_operatingSiteOptions, _operatingSites);
				if (dialog.ShowDialog(this) == DialogResult.OK)
				{
					_operatingSites = dialog.SelectedOperatingSites.ToDictionary((KeyValuePair<string, List<string>> pair) => pair.Key, (KeyValuePair<string, List<string>> pair) => pair.Value.ToList(), StringComparer.OrdinalIgnoreCase);
					_aliasStatus.Text = OperatingSiteSummary();
				}
			};
			aliasGroup.Controls.Add(operatingSitesButton);
			base.Controls.Add(aliasGroup);
			y += 116;
			GroupBox authGroup = new GroupBox
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
			Button addAccount = new Button
			{
				Text = "新增账号授权",
				Left = 14,
				Top = 196,
				Width = 120
			};
			addAccount.Click += async delegate
			{
				await RunDialogActionAsync(AuthorizeRequestedAsync);
			};
			Button completeAuth = new Button
			{
				Text = "粘贴授权结果",
				Left = 150,
				Top = 196,
				Width = 120
			};
			completeAuth.Click += async delegate
			{
				await RunDialogActionAsync(CompleteAuthorizationRequestedAsync);
			};
			Button refreshAccounts = new Button
			{
				Text = "刷新账号/店铺",
				Left = 286,
				Top = 196,
				Width = 120
			};
			refreshAccounts.Click += async delegate
			{
				await RunDialogActionAsync(RefreshAccountsRequestedAsync);
			};
			authGroup.Controls.Add(_authSummary);
			authGroup.Controls.Add(_authStatus);
			authGroup.Controls.Add(_callbackInput);
			authGroup.Controls.Add(addAccount);
			authGroup.Controls.Add(completeAuth);
			authGroup.Controls.Add(refreshAccounts);
			base.Controls.Add(authGroup);
			y += 256;
			GroupBox advancedGroup = new GroupBox
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
			int advancedY = 24;
			AddRowTo(advancedGroup, "诊断文件目录", _outputDir, outputDir, ref advancedY);
			AddNumberRowTo(advancedGroup, "读取并发（高级）", _readConcurrency, readConcurrency, 1m, 20m, ref advancedY);
			AddNumberRowTo(advancedGroup, "活动并发（高级）", _previewConcurrency, previewConcurrency, 1m, 20m, ref advancedY);
			AddNumberRowTo(advancedGroup, "商品写入并发（当前使用值）", _writeConcurrency, writeConcurrency, 1m, 700m, ref advancedY);
			base.Controls.Add(advancedGroup);
			ReloadAliasAccounts(accounts, storeAliases);
			Button ok = new Button
			{
				Text = "保存",
				Left = 480,
				Width = 80,
				Top = 674,
				DialogResult = DialogResult.OK
			};
			Button cancel = new Button
			{
				Text = "取消",
				Left = 576,
				Width = 80,
				Top = 674,
				DialogResult = DialogResult.Cancel
			};
			base.Controls.Add(ok);
			base.Controls.Add(cancel);
			UiTheme.ApplyControlTree(this);
			UiTheme.StylePrimaryButton(ok);
			UiTheme.StyleButton(cancel, primary: false);
			UiTheme.StyleButton(addAccount, primary: false);
			UiTheme.StyleButton(completeAuth, primary: false);
			UiTheme.StyleButton(refreshAccounts, primary: false);
			UiTheme.StyleButton(saveAlias, primary: false);
			UiTheme.StyleButton(operatingSitesButton, primary: false);
			UiTheme.StyleButton(advancedToggle, primary: false);
			advancedToggle.Click += delegate
			{
				advancedGroup.Visible = !advancedGroup.Visible;
				advancedToggle.Text = (advancedGroup.Visible ? "收起高级" : "高级设置");
				ok.Top = (advancedGroup.Visible ? 842 : 674);
				cancel.Top = ok.Top;
				base.ClientSize = (advancedGroup.Visible ? new Size(680, 930) : new Size(680, 760));
			};
			base.AcceptButton = ok;
			base.CancelButton = cancel;
		}

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

		private string OperatingSiteSummary()
		{
			int total = _operatingSites.Values.Sum((List<string> sites) => sites.Distinct(StringComparer.OrdinalIgnoreCase).Count());
			return total > 0 ? $"已设置经营站点 {total} 个；未勾选站点不参与活动检测和批量操作。" : "请设置实际经营站点；程序会按活跃商品提供建议。";
		}

		public void ReloadAliasAccounts(IReadOnlyList<AccountInfo> accounts, IReadOnlyDictionary<string, string> storeAliases)
		{
			_currentAliases = new Dictionary<string, string>(storeAliases, StringComparer.OrdinalIgnoreCase);
			string selected = SelectedAliasAccountId;
			_aliasAccountSelect.Items.Clear();
			foreach (AccountInfo account in accounts)
			{
				string site = ((account.SiteId.Length > 0) ? (" | " + account.SiteId) : "");
				_aliasAccountSelect.Items.Add(new ComboItem(account.AccountId, account.StoreName + site));
			}
			if (_aliasAccountSelect.Items.Count == 0)
			{
				_aliasName.Text = "";
				return;
			}
			int index = 0;
			for (int i = 0; i < _aliasAccountSelect.Items.Count; i++)
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
			if (_aliasAccountSelect.SelectedItem is ComboItem item)
			{
				if ((aliases ?? _currentAliases).TryGetValue(item.Value, out string? alias))
				{
					_aliasName.Text = alias;
				}
				else
				{
					_aliasName.Text = item.Text.Split('|')[0].Trim();
				}
			}
		}

		private async Task RunDialogActionAsync(Func<Task>? action)
		{
			if (action == null)
			{
				return;
			}
			try
			{
				base.UseWaitCursor = true;
				await action();
			}
			catch (Exception ex)
			{
				SetAuthorizationStatus("操作失败：" + ex.Message);
				MessageBox.Show(ex.Message, "美客多折扣管家", MessageBoxButtons.OK, MessageBoxIcon.Hand);
			}
			finally
			{
				base.UseWaitCursor = false;
			}
		}

		private void AddRow(string labelText, TextBox textBox, string value, ref int y)
		{
			base.Controls.Add(new Label
			{
				Text = labelText,
				Left = 16,
				Top = y + 4,
				Width = 120,
				ForeColor = UiTheme.MainText,
				BackColor = Color.Transparent
			});
			textBox.Left = 145;
			textBox.Top = y;
			textBox.Width = 500;
			textBox.Text = value;
			UiTheme.StyleTextBox(textBox);
			base.Controls.Add(textBox);
			y += 34;
		}

		private void AddNumberRow(string labelText, NumericUpDown number, decimal value, decimal min, decimal max, ref int y)
		{
			base.Controls.Add(new Label
			{
				Text = labelText,
				Left = 16,
				Top = y + 4,
				Width = 120,
				ForeColor = UiTheme.MainText,
				BackColor = Color.Transparent
			});
			number.Left = 145;
			number.Top = y;
			number.Width = 90;
			number.Minimum = min;
			number.Maximum = max;
			number.DecimalPlaces = 0;
			number.Increment = 1m;
			number.Value = Math.Max(min, Math.Min(max, value));
			UiTheme.StyleNumber(number);
			base.Controls.Add(number);
			y += 34;
		}

		private void ConfigureHiddenNumber(NumericUpDown number, decimal value, decimal min, decimal max)
		{
			number.Minimum = min;
			number.Maximum = max;
			number.DecimalPlaces = 0;
			number.Increment = 1m;
			number.Value = Math.Max(min, Math.Min(max, value));
			UiTheme.StyleNumber(number);
		}

		private void AddRowTo(Control parent, string labelText, TextBox textBox, string value, ref int y)
		{
			parent.Controls.Add(new Label
			{
				Text = labelText,
				Left = 14,
				Top = y + 4,
				Width = 132,
				ForeColor = UiTheme.MainText,
				BackColor = Color.Transparent
			});
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
			parent.Controls.Add(new Label
			{
				Text = labelText,
				Left = 14,
				Top = y + 4,
				Width = 170,
				ForeColor = UiTheme.MainText,
				BackColor = Color.Transparent
			});
			number.Left = 190;
			number.Top = y;
			number.Width = 90;
			number.Minimum = min;
			number.Maximum = max;
			number.DecimalPlaces = 0;
			number.Increment = 1m;
			number.Value = Math.Max(min, Math.Min(max, value));
			UiTheme.StyleNumber(number);
			parent.Controls.Add(number);
			y += 30;
		}
	}

	private const int Port = 28758;

	private const string Url = "http://127.0.0.1:28758";

	private const string HealthUrl = "http://127.0.0.1:28758/api/health";

	private const string AuthDir = "C:\\Users\\dztf6\\Documents\\美客多授权";

	private static readonly object ServiceLogLock = new object();

	private static readonly object ServiceStartLock = new object();

	private static Task<Process?>? ServiceStartTask;

	[STAThread]
	private static int Main(string[] args)
	{
		bool noOpen = Array.Exists(args, (string arg) => string.Equals(arg, "--no-open", StringComparison.OrdinalIgnoreCase));
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
				AppendInternalDiagnostic("application startup failed", ex);
				MessageBox.Show(ProductFacingErrorMessage(ex), "美客多折扣管家", MessageBoxButtons.OK, MessageBoxIcon.Hand);
			}
			catch
			{
			}
			return 1;
		}
	}

	private static async Task<Process?> EnsureServiceAsync()
	{
		if (await IsHealthy())
		{
			return null;
		}
		Task<Process?> task;
		lock (ServiceStartLock)
		{
			if (ServiceStartTask == null || ServiceStartTask.IsCompleted)
			{
				ServiceStartTask = EnsureServiceCoreAsync();
			}
			task = ServiceStartTask;
		}
		try
		{
			return await task;
		}
		catch
		{
			lock (ServiceStartLock)
			{
				if (ServiceStartTask == task)
				{
					ServiceStartTask = null;
				}
			}
			throw;
		}
	}

	private static async Task<Process?> EnsureServiceCoreAsync()
	{
		string root = GetInstallRoot();
		string appDir = Path.Combine(root, "app");
		string dataDir = Path.Combine(root, "data");
		string logDir = Path.Combine(dataDir, "logs");
		string stdoutLog = Path.Combine(logDir, "server.out.log");
		string stderrLog = Path.Combine(logDir, "server.err.log");
		if (await IsHealthy())
		{
			return null;
		}
		int? portOwner = GetPortOwnerPid();
		if (portOwner.HasValue)
		{
			if (!IsOwnNodeService(portOwner.Value, root))
			{
				AppendInternalDiagnostic($"component channel occupied by {DescribeProcess(portOwner.Value)}");
				throw new InvalidOperationException("程序组件启动失败：当前电脑上已有其它程序占用了必要通道。请关闭其它折扣管家窗口或重启软件后再试。");
			}
			StopProcessTree(portOwner.Value);
			await Task.Delay(800);
		}
		Directory.CreateDirectory(root);
		Directory.CreateDirectory(dataDir);
		Directory.CreateDirectory(logDir);
		ExtractPayload(root);
		string nodeExe = Path.Combine(root, "node", "node.exe");
		string serverJs = Path.Combine(appDir, "src", "server.js");
		if (!File.Exists(nodeExe))
		{
			throw new FileNotFoundException("程序安装包不完整，缺少运行组件。请重新覆盖安装。", nodeExe);
		}

		if (!File.Exists(serverJs))
		{
			throw new FileNotFoundException("程序安装包不完整，缺少业务组件。请重新覆盖安装。", serverJs);
		}
		File.AppendAllText(stdoutLog, $"{DateTime.Now:yyyy-MM-dd HH:mm:ss} starting service: {nodeExe} src/server.js{Environment.NewLine}", Encoding.UTF8);
		ProcessStartInfo psi = new ProcessStartInfo
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
		psi.Environment["ML_STANDALONE_AUTH_DIR"] = "C:\\Users\\dztf6\\Documents\\美客多授权";
		Process process = Process.Start(psi) ?? throw new InvalidOperationException("程序组件启动失败。请关闭软件后重新打开；如果仍出现，请把诊断信息发给我处理。");
		AttachServiceLogs(process, stdoutLog, stderrLog);
		for (int i = 0; i < 60; i++)
		{
			if (await IsHealthy())
			{
				return process;
			}
			if (process.HasExited)
			{
				AppendInternalDiagnostic($"component process exited with code {process.ExitCode}. stderr tail: {ReadTail(stderrLog)}");
				throw new InvalidOperationException("程序组件启动后异常退出。请关闭软件后重新打开；如果仍出现，请把诊断信息发给我处理。");
			}
			await Task.Delay(500);
		}
		AppendInternalDiagnostic($"component startup timed out. port owner: {DescribeCurrentPortOwner()}, dataDir: {dataDir}, logDir: {logDir}");
		throw new TimeoutException("程序组件启动时间过长。请稍等后重开软件；如果仍出现，请把诊断信息发给我处理。");
	}

	private static void AttachServiceLogs(Process process, string stdoutLog, string stderrLog)
	{
		string stdoutLog2 = stdoutLog;
		string stderrLog2 = stderrLog;
		process.OutputDataReceived += delegate(object _, DataReceivedEventArgs e)
		{
			if (!string.IsNullOrEmpty(e.Data))
			{
				AppendServiceLog(stdoutLog2, e.Data);
			}
		};
		process.ErrorDataReceived += delegate(object _, DataReceivedEventArgs e)
		{
			if (!string.IsNullOrEmpty(e.Data))
			{
				AppendServiceLog(stderrLog2, e.Data);
			}
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
			using Process process = new Process();
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
			string text = process.StandardOutput.ReadToEnd();
			process.WaitForExit(5000);
			string[] array = text.Split(new char[2] { '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries);
			for (int i = 0; i < array.Length; i++)
			{
				string[] parts = array[i].Split(' ', StringSplitOptions.RemoveEmptyEntries);
				if (parts.Length >= 5 && parts[0].Equals("TCP", StringComparison.OrdinalIgnoreCase) && parts[3].Equals("LISTENING", StringComparison.OrdinalIgnoreCase) && parts[1].EndsWith($":{28758}", StringComparison.OrdinalIgnoreCase) && int.TryParse(parts[4], out var pid))
				{
					return pid;
				}
			}
		}
		catch
		{
		}
		return null;
	}

	private static bool IsOwnNodeService(int pid, string root)
	{
		try
		{
			using Process process = Process.GetProcessById(pid);
			string obj = process.MainModule?.FileName ?? "";
			string expectedNodeDir = Path.Combine(root, "node");
			return obj.StartsWith(expectedNodeDir, StringComparison.OrdinalIgnoreCase);
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
			using Process process = Process.GetProcessById(pid);
			process.Kill(entireProcessTree: true);
			process.WaitForExit(3000);
		}
		catch
		{
		}
	}

	private static string DescribeCurrentPortOwner()
	{
		int? owner = GetPortOwnerPid();
		if (!owner.HasValue)
		{
			return "未发现监听进程";
		}
		return DescribeProcess(owner.Value);
	}

	private static string DescribeProcess(int pid)
	{
		try
		{
			using Process process = Process.GetProcessById(pid);
			string path = "";
			try
			{
				path = process.MainModule?.FileName ?? "";
			}
			catch
			{
			}
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
			if (!File.Exists(path))
			{
				return "无错误日志";
			}
			string text = File.ReadAllText(path, Encoding.UTF8);
			if (text.Length <= 1200)
			{
				return text.Trim();
			}
			int length = text.Length;
			int num = length - 1200;
			return text.Substring(num, length - num).Trim();
		}
		catch (Exception ex)
		{
			return "读取日志失败：" + ex.Message;
		}
	}

	private static void ExtractPayload(string root)
	{
		string markerPath = Path.Combine(root, "payload.version");
		string appServer = Path.Combine(root, "app", "src", "server.js");
		string nodeExe = Path.Combine(root, "node", "node.exe");
		FileInfo exeInfo = new FileInfo(Application.ExecutablePath);
		string payloadVersion = $"{exeInfo.Length}:{exeInfo.LastWriteTimeUtc.Ticks}";
		if (File.Exists(markerPath) && File.Exists(appServer) && File.Exists(nodeExe) && string.Equals(File.ReadAllText(markerPath, Encoding.UTF8).Trim(), payloadVersion, StringComparison.Ordinal))
		{
			return;
		}
		using Stream stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("payload.zip") ?? throw new InvalidOperationException("完整包中缺少 payload。");
		string tempZip = Path.Combine(root, "payload.zip");
		using (FileStream output = File.Create(tempZip))
		{
			stream.CopyTo(output);
		}
		ZipFile.ExtractToDirectory(tempZip, root, overwriteFiles: true);
		File.Delete(tempZip);
		File.WriteAllText(markerPath, payloadVersion, Encoding.UTF8);
	}

	private static string GetInstallRoot()
	{
		return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "MercadoDiscountManagerStandalone");
	}

	private static string ProductFacingErrorMessage(Exception ex)
	{
		return ProductFacingErrorMessage(ex.Message);
	}

	private static string ProductFacingErrorMessage(string message)
	{
		if (string.IsNullOrWhiteSpace(message))
		{
			return "当前操作没有完成。请稍后重试；如果仍然出现，请把诊断信息发给我处理。";
		}
		string clean = message.Trim();
		if (clean.Contains("本地服务", StringComparison.OrdinalIgnoreCase) || clean.Contains("端口", StringComparison.OrdinalIgnoreCase) || clean.Contains("日志目录", StringComparison.OrdinalIgnoreCase) || clean.Contains("An error occurred while sending the request", StringComparison.OrdinalIgnoreCase) || clean.Contains("No connection could be made", StringComparison.OrdinalIgnoreCase) || clean.Contains("actively refused", StringComparison.OrdinalIgnoreCase))
		{
			return "程序组件暂时不可用，已尝试自动修复但没有成功。请关闭软件后重新打开；如果仍然出现，请把诊断信息发给我处理。";
		}
		if (clean.Contains("requires an element of type 'Object'", StringComparison.OrdinalIgnoreCase) || clean.Contains("target element has type 'Null'", StringComparison.OrdinalIgnoreCase) || clean.Contains("JsonElement", StringComparison.OrdinalIgnoreCase))
		{
			return "任务已结束，但后台没有返回完整汇总；已完成结果已保存，请查看历史记录。";
		}
		return clean;
	}

	private static void AppendInternalDiagnostic(string message)
	{
		AppendInternalDiagnostic(message, null);
	}

	private static void AppendInternalDiagnostic(string message, Exception? error)
	{
		try
		{
			string logDir = Path.Combine(GetInstallRoot(), "data", "logs");
			Directory.CreateDirectory(logDir);
			string line = $"{DateTime.Now:yyyy-MM-dd HH:mm:ss} {message}";
			if (error != null)
			{
				line += $" | {error.GetType().Name}: {error.Message}";
			}
			File.AppendAllText(Path.Combine(logDir, "desktop-diagnostics.log"), line + Environment.NewLine, Encoding.UTF8);
		}
		catch
		{
		}
	}

	private static async Task<bool> IsHealthy()
	{
		try
		{
			using HttpClient client = new HttpClient
			{
				Timeout = TimeSpan.FromSeconds(2.0)
			};
			using JsonDocument doc = JsonDocument.Parse(await client.GetStringAsync("http://127.0.0.1:28758/api/health"));
			JsonElement ok;
			return doc.RootElement.TryGetProperty("ok", out ok) && ok.GetBoolean();
		}
		catch
		{
			return false;
		}
	}
}
