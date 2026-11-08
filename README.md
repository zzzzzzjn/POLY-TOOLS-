# POLY TOOLS · Mono → Poly WAV

本地离线工具，将 1–128 个单声道 WAV 按用户明确指定的顺序交织为一个多通道 WAV。采样字节原样搬运，不混音、不改变增益、位深或采样率；运行时不需要联网、账号或音频设备。

**尚无真实 TruSound 录音或目标播放器的验证。** 多通道 WAV 并不是 ADM 或 TruSound 工程；不包含对象坐标、轨迹等控制数据。目标软件可能不支持 128 通道、RF64、部分有效位数或未指定扬声器位置的格式。

## 桌面界面

### 已打包的 macOS 应用

Apple 芯片（arm64）版本位于 `release/mac-arm64/POLY TOOLS.app`，也可解压 `release/POLY TOOLS-1.0.0-arm64-mac.zip` 后打开应用。应用内置音频核心和 Python 运行时，无需安装 Node.js、npm 或 Python，也无需联网。本版本未使用 Apple Developer ID 签名或公证，macOS 可能阻止直接打开；确认来源后，可在 Finder 中右键应用选择“打开”，或到“系统设置 → 隐私与安全性”查看允许打开选项。请勿绕过来源不明的安全提示。

此构建仅针对 Apple 芯片，未生成 Intel 版本，也未在其他 macOS 版本或另一台电脑上测试。

### 源码启动

在 macOS 上可双击 `Start POLY TOOLS.command`，或在本目录的终端运行：

```sh
npm start
```

需要 Node.js 18 或以上、npm 和 Python 3.9 或以上。首次使用或依赖未安装时，在本目录执行 `npm install`（这一步需要访问 npm 下载依赖）；安装后运行和处理音频不需要联网。若 `python3` 不在 PATH，可在终端以 `POLY_TOOLS_PYTHON=/path/to/python3 npm start` 指定解释器。

### 重新打包

在 Apple 芯片 Mac 上，安装依赖后执行：

```sh
npm install
python3 -m venv .venv-build
.venv-build/bin/python -m pip install -r requirements-build.txt
npm run package:mac
npm run test:packaged
```

打包会生成 `.app` 和 ZIP，不会进行 Apple 签名、公证或自动更新。打包时用到的 Electron 和 PyInstaller 依赖需下载；最终应用处理音频时不联网。

通过 **Add files** 或拖放添加文件，选中行后可用上/下按钮或鼠标拖动调整顺序、删除或清空。选择保存目录，检查通道顺序与录音段，确认按第 0 帧对齐，预检通过后点击 **Merge files**。处理和校验显示后端真实进度，可取消；仅校验通过才显示完成。`Review details` 展示具体格式、帧数、时间参考、候选通道号及错误。已有输出不会覆盖。

浏览器运行 `npm run dev` 仅用于界面预览，标有 **Demo · No audio is processed**，没有本机文件访问能力，不会执行或模拟成功合并。左上品牌图形和 `assets/icon.svg` 是可替换的参考占位图，不是正式品牌母版。

界面及异常状态截图位于 `screenshots/`，截图中的音频数据均为合成测试文件。桌面桥通过本地 Python 子进程调用 `polywav.py`，不会把音频上传或载入网页内存。真实 TruSound 录音与目标软件导入仍待验证。

## 命令行使用

需要 Python 3.9 或以上。在本目录运行：

```sh
python3 polywav.py inspect /path/to/recording
```

`inspect` 仅扫描目录当前层，并显示头信息和候选通道号；候选号只是提示，**不会自动生成或确认映射**。检查文件列表，确认属于同一段同步录音，并确认没有混入其他 take 或分段。对于重复候选号、疑似分段、缺号或无法确定编号，按提示先整理文件。文件名本身无法可靠验证 take 或起点。

在录音文件所在目录手动创建 `channels.json`，按需要的输出通道顺序填写每个文件，例如：

```json
{
  "schema_version": 1,
  "alignment": "sample_zero_confirmed",
  "channels": [
    {"output_channel": 1, "input": "CH001.wav"},
    {"output_channel": 2, "input": "CH002.wav"}
  ]
}
```

路径相对于清单文件，亦支持绝对路径；输出通道必须从 1 开始连续排列，不得重复输入。顺序可调整，但清单中 `output_channel` 必须按顺序排列。仅在**已确认来自同一段同步录音并同意按第 0 帧对齐**时填写上述 `alignment`。若全部文件都有 BWF TimeReference，数值必须一致；缺失时会提示“起点未验证”。即使参考值一致，也不能证明文件一定属于同一 take。

```sh
python3 polywav.py merge --manifest /path/to/recording/channels.json --output /path/to/output/take_poly.wav
# 需要时强制 RF64，即使数据量很小
python3 polywav.py merge --manifest /path/to/recording/channels.json --output /path/to/output/take_rf64.wav --container rf64
# 以后可以重新读取原始输入及输出，重新校验
python3 polywav.py verify --manifest /path/to/recording/channels.json --output /path/to/output/take_poly.wav
```

合并总是强制执行全量逐通道 SHA-256 校验；`--verify` 可选，但不会关闭或改变校验。程序显示预估大小、写入和校验进度；按 Ctrl-C 取消。零帧输入允许，所有输入必须同为零帧。输入、输出 WAV 和同名 JSON 均不会被覆盖。临时文件在目标目录创建，校验后通过不覆盖的硬链接发布；文件系统若不支持硬链接会明确报错，不能保证所有网络或特殊文件系统都支持此发布方式。异常取消会尽力清理本次临时文件；强制终止或断电可能留下 `.polywav-*.tmp`，应先确认没有运行中的任务再手动处理。输出和 JSON 是两个文件，不可能作为一个事务原子发布；崩溃时可能只留下报告，须检查并手动处理。

同名 JSON 记录格式、相对报告目录的输入路径、通道映射、对齐依据、输入时间参考、每通道哈希和校验结果。输出只包含新建的音频格式头和音频数据，**不保留嵌入式 BWF 时间码、iXML 或其他输入元数据**。`verify` 直接重新计算输入和输出哈希，不修改 JSON。

## 支持与限制

- 小端 RIFF/WAVE 和 RF64/WAVE；经典及 WAVEFORMATEXTENSIBLE 的 PCM 16/24/32 位、IEEE float32。
- 输入须为 mono，采样率、编码、容器位数、有效位数和帧数完全一致；不补静音、截断或转换。
- 接受并跳过未知 chunk 和偶数字节填充；拒绝 RIFX、Wave64、压缩、多个 data、wavl、损坏文件和未整理的重复通道分段。候选通道号全部可识别时，必须无重复且不缺号；不能识别时只能依靠用户明确清单检查，程序无法识别任意命名的错误 take。
- 输出使用 WAVEFORMATEXTENSIBLE、未指定扬声器布局的零 channel mask；float 输出含 fact。按完整文件大小自动选 RIFF 或 RF64，RF64 使用 ds64 和大小哨兵。
- 内存随通道数与 8192 帧块大小变化，不随录音时长线性增长；需要同时打开全部输入，文件描述符不足会报错。预计空间与文件系统限制仅能尽力预检，实际写入仍可能因设备、权限或容量问题失败。

## 测试与验证记录

```sh
python3 -m unittest discover -s tests -v
npm test
npm run test:desktop
# 可选：真实写入并校验 >4 GiB；需要至少 9 GiB 临时空间
POLYWAV_LARGE_TEST=1 python3 -m unittest discover -s tests -p test_large.py -v
```

合成测试覆盖 1/2/12/64/128 通道及显式倒序、非整块与零帧、PCM/float 特殊位模式、RF64、奇数 chunk、格式/长度/时间不匹配、损坏文件、取消、空间不足、输入修改、发布竞争和输出篡改。macOS 可用时，测试还用系统独立读取器 `afinfo` 检查真实合并输出的 2 通道、48 kHz、24-bit PCM 和帧数；其他系统上该检查跳过。独立读取器不会自动对每个用户文件运行，JSON 如实标记其未执行。

本次验证环境：macOS 15.7.3 (24G419)、arm64、Python 3.9.6；系统 `afinfo`（随该 macOS 构建提供）通过上述读取检查。一次非稀疏大文件测试实际写入并校验了 4,294,967,300 字节 RF64 音频数据；对比 64 MiB 输入，进程峰值 RSS 从 19,611,648 增至 20,856,832 字节，整个测试约 5.9 秒。这只是该环境下单通道合成数据的记录，不代表 128 通道性能或其他设备表现。

**待完成：** 真实 TruSound 录音、目标软件导入及 128 路通道对应验证；尚不能宣称目标播放器兼容。

格式参考：[EBU Tech 3306](https://tech.ebu.ch/publications/tech3306)、[Microsoft WAVEFORMATEXTENSIBLE](https://learn.microsoft.com/en-us/windows/win32/api/mmreg/ns-mmreg-waveformatextensible)。
