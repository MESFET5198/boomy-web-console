# Noetix Boomy 控制与编排项目交接文档

> 最后更新：2026-11-20  
> GitHub 仓库：`https://github.com/MESFET5198/boomy-web-console`（分支 `main`）

---

## 1. 机器人硬件与开发模式说明

- **机型确认**：手头这台为 **Boomy 标准版**（不带内置 Jetson Orin 二开算力板）。
  - 无线 IP 的 `2222` 端口（算力板 SSH 转发端口）握手会被重置；`22` 端口为运控板（瑞芯微 RK3576），**官方文档明令禁止登录运控板**。
- **开放能力**：
  - ✅ 支持 **`HighController`**（高层动作调度、速度控制、状态读取：IMU、BMS、21 关节电机、`workmode`）。
  - ❌ 不支持 `LowController`（500 Hz 底层电机控制）与 `MediaController`（仅 EDU 版开放）。
- **外挂开发机制**：
  - 运控板固定 IP 为 **`192.168.55.102`**，且**仅允许与固定 IP `192.168.55.101` 进行 CycloneDDS 通信**。
  - 官方 SDK（`noetix_sdk_boomy`，基于 CycloneDDS 0.11.0）**仅提供 Linux 库**（`x86_64` / `aarch64`），因此运行在 WSL2 Ubuntu 24.04 中。

---

## 2. 本机环境与网络架构

### 2.1 关键目录
- **Git 仓库（开发/推送用）**：`C:\Users\Work42\.gemini\antigravity\scratch\boomy-web-console`
- **运行目录（含已编译 SDK）**：`C:\Users\Work42\.gemini\antigravity\scratch\noetix_sdk_boomy`
  - 已在 WSL2 Ubuntu 24.04 中编译完成 `build/example_high` 与 `build/highcontrol_py.cpython-312-x86_64-linux-gnu.so`。
  - 注意：在 Windows 下 `git clone` 官方 SDK 会破坏 `dds/x86_64/lib/*.so` 的软链接，已在 WSL2 下用 `git -c core.symlinks=true checkout -- dds/ lib/` 修复。

### 2.2 WSL2 网络桥接配置（为什么用虚拟网桥）
- **为什么不能直接让 WSL2 接管 `以太网 12`**：
  - `以太网 12`（Realtek 2.5GbE）是主板 PCIe 网卡，Windows 10/11 桌面版的 WSL2 不支持 PCIe 设备直通（DDA 仅 Windows Server 支持；仅 USB 网卡可通过 `usbipd-win` 透传）。
  - Windows 10（Build 19045）不支持 Windows 11 的 `networkingMode=mirrored`；而默认 NAT 模式下 WSL2 内部 IP 为 `172.24.x.x`，运控板发往 `192.168.55.101` 的 DDS UDP 回包无法穿透 NAT。
- **当前稳定方案**：
  - 已启用 Hyper-V，并创建外部虚拟交换机 **`WSLBridge`** 绑定到接了 55 网段二级路由器网线的 **`以太网 12`**（`-AllowManagementOS $true`，保留宿主机访问权限，使 Windows 浏览器可直接打开 `http://192.168.55.101:8080`）。
  - 配置文件 `C:\Users\Work42\.wslconfig`：
    ```ini
    [wsl2]
    networkingMode=bridged
    vmSwitch=WSLBridge
    ```
- **已知坑：二级路由器 DHCP 抢占副 IP**：
  - 长时间运行后，二级路由器 DHCP 可能会给 WSL2 `eth0` 下发第二个 IP（如 `192.168.55.126`）并将发往 `192.168.55.102` 的源地址切到副 IP，导致机器人断流。
  - **解决方法**：运行 `./setup_ip.sh` 清除动态 IP 并强制锁定 `src 192.168.55.101`，然后重启 `web/server.py`。

---

## 3. 日常启动步骤

在 **WSL2 Ubuntu** 终端中执行：

```bash
cd /mnt/c/Users/Work42/.gemini/antigravity/scratch/noetix_sdk_boomy

# 1. 锁定 192.168.55.101 并检查与运控板 192.168.55.102 连通性
./setup_ip.sh
sudo ip route replace 192.168.55.0/24 dev eth0 src 192.168.55.101
ping -c 2 192.168.55.102

# 2. 启动 Web 控制后端（绑定 0.0.0.0:8080）
python3 web/server.py --host 0.0.0.0 --port 8080
```

在浏览器打开：**`http://192.168.55.101:8080`**（同网段其他设备亦可访问）。

---

## 4. 功能与接口一览

### 4.1 后端服务（`web/server.py`）
- 零第三方依赖（Python 标准库 `ThreadingHTTPServer`），绑定 `0.0.0.0:8080`。
- 控制循环：**100 Hz（10 ms）**，动作指令走线程安全队列（边沿触发，下一帧自动恢复 `DEFAULT`），速度带 **0.6 s 看门狗**（超时自动归零），切模式前自动清零速度。
- 状态回调：对运控板 500 Hz 回调做了 **~20 Hz 节流解析**（`workmode` 变化 0 延迟更新），避免 Python GIL 争抢造成控制抖动。
- **HTTP API**：
  - `GET /`：中/英文双语控制前端
  - `GET /api/status`：返回 `connected`、`age_ms`、`cmd_vel`、`status`（`workmode`、`bms`、`imu`、`motors[21]`）
  - `POST /api/cmd`：`{"action": "SWING", "index": 0}`
  - `POST /api/vel`：`{"x": 0.4, "y": 0.0, "z": 0.0}`（范围 `[-1, 1]`；`WALK` 下仅 `x, z` 有效，`FULLWALK` 下 `y` 横移有效）
  - `POST /api/stop`：速度清零 + 清空动作队列，**绝不发 `START`**
  - `POST /api/disable`：需 `{"confirm": true}`；站立类模式 `{2,3,5,8,9,10,13,31,32,33}` 未带 `"force": true` 时返回 **HTTP 409** 拦截防摔倒
  - `POST /api/estop`：已弃用，行为等价 `/api/stop`

### 4.2 前端控制台（`web/index.html`）
- **中 / 英文双语切换**（右上角按钮，`localStorage` 持久化）。
- **动作编排台（Choreography Sequencer）**：支持一键加载 `片尾节目 (Outro)` / `横移节目 (Crab)`，自由增删改排序 `MODE / MOVE / ACTION / PAUSE` 步骤，运行中实时高亮步骤与打印 `workmode` 变化日志。
- **手动控制**：虚拟摇杆 + 方向按钮 + 键盘 `WASD / Q E` + 空格键停止移动。
- **实时遥测**：连接延迟、`workmode`、BMS 电池、IMU、21 关节电机位置/转速/扭矩/温度/错误码。

### 4.3 命令行编排脚本与自动测试
- `scripts/choreo_demo.py`（29 行极简上镜脚本）& `scripts/choreo_lib.py`（纯 `urllib` 客户端）
  ```bash
  # 预览（不发请求）
  python3 scripts/choreo_demo.py --dry-run
  # 保守上机单步测试
  python3 scripts/choreo_demo.py --server http://192.168.55.101:8080 --program outro --speed-scale 0.3 --step
  ```
- 自动测试（无需真机，覆盖 8 项安全与状态机用例）：
  ```bash
  cd /mnt/c/Users/Work42/.gemini/antigravity/scratch/boomy-web-console
  python3 -m unittest discover -s tests -v
  ```

---

## 5. 安全规范与待实测事项

### 必须遵守的安全规范
1. **地面无吊架严禁点击「准备模式 (`SWITCH`)」**：`workmode=1` 无动态平衡算法，地面进入会直接倒地。地面标准流程应为：**平躺 → `倒地起身 (FALLTOSTAND)` → 自动进入 `半身走路 (2)` → 结束时 `一键收纳 (STANDTOFALL)`**。
2. **站立时禁止发 `START`**：非失能状态发 `START` 会立刻全身卸力倒地。平时急停/中断一律用 **`停止移动 (STOP)`**（`/api/stop`）。
3. **使用 SDK 时保持手机 App 断开**，避免双端指令冲突。
4. 调试区域保持至少 **4 m × 2 m** 空旷地面，随时准备硬件急停（后脑勺按钮）。

### 待上机实测确认项
1. **`crab` 节目中 `FULLWALK (3)` 执行 `CHEER` 后返回的 `workmode`**：文档状态机标明预设动作结束后自动回到 `半身走路 (2)`；若真机回到 `2` 而非 `3`，需将编排结束判定放宽为 `wm in (2, base_wm)`。
2. **`PLAYTEACH (13)` 结束后是否自动回到 `2`**：文档注明示教播放完可能停在结束姿态需手动回走路模式，需根据首次上机日志确认是否在其后自动补发 `mode("WALK")`。
