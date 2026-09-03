# ⚡ HSCast — High-Speed Screen Casting & Control

> Fast, smooth, and low-latency two-way screen casting between **Android** and **Windows** with full mouse and keyboard control.

---

## 🌟 What is HSCast?

**HSCast** lets you seamlessly share screens between your Android phone and Windows PC over a **USB cable** or your local **Wi-Fi**:

* 📱 ➔ 💻 **Phone to PC (Mirroring)**: View your phone's display on your PC with full mouse, touch gesture, and keyboard control (like *scrcpy*).
* 💻 ➔ 📱 **PC to Phone (Desktop)**: Stream your Windows PC monitor directly onto your phone screen.
* ⚡ **Ultra-Low Latency**: High frame rates (up to 60+ FPS) with instant hardware-accelerated streaming.

---

## 📸 Overview & Live Demo

### Pictorial Overview
![HSCast Overview](assets/hscast_overview.png)

### Live Demo (In Action)
![HSCast Live Demo](assets/hscast_demo.gif)

---

## 📱 How to Run on Android

### Step 1: Install the Android App
* Open the `android/` folder in **Android Studio** and click **Run**, or build the APK using:
  ```bash
  cd android
  .\build-apk.bat
  ```
* Install the generated `.apk` onto your phone.

### Step 2: Enable Phone Settings
1. **USB Debugging** *(for USB cable connection)*:
   * Go to phone **Settings → Developer Options → Enable USB Debugging**.
2. **Remote Input Control** *(optional, to control phone with PC mouse & keyboard)*:
   * Go to phone **Settings → Accessibility → HSCast remote input → Enable**.

### Step 3: Start Casting
* Open the **HSCast** app on your phone.
* Select your mode:
  * **Cast Phone Screen** (to stream to PC).
  * **Receive PC Screen** (to view PC screen on phone).
* Tap **Start Casting** and accept the screen capture prompt.

---

## 💻 Windows Setup & First-Time Dependencies

Before using HSCast for the first time on a Windows PC, ensure you have the following installed:

### 1. Required Software
* **Python 3.12 or 3.13 (64-bit)**:
  * Download from [python.org](https://www.python.org/downloads/).
  * ⚠️ **Important**: Check the box **"Add python.exe to PATH"** during installation.
* **Android Platform Tools (ADB)** *(Required only for USB cable mode)*:
  * Download [Platform-Tools for Windows](https://developer.android.com/tools/releases/platform-tools).
  * Extract the folder (e.g., `C:\platform-tools`).
  * You can add it to your Windows System `PATH`, or simply point to `adb.exe` directly inside the HSCast Windows app via the **System Doctor** tab!
  *(Not needed if you cast over Wi-Fi).*

### 2. Python Dependencies
HSCast handles all Python dependencies automatically:
* **Automatic Install**: When you run `Launch-HSCast.bat` or `.\run.ps1`, it will automatically create a virtual environment (`.venv`) and install all required libraries (`av`, `PySDL2`, `numpy`, `dxcam`, `pywebview`, etc.).
* **Manual Install (Optional)**:
  ```powershell
  cd windows
  python -m venv .venv
  .\.venv\Scripts\activate
  pip install -r requirements.txt
  ```

---

## 🚀 How to Run on Windows (Quick Start)

### Option 1: 1-Click Graphical Launcher (Easiest)
Simply double-click the launcher file in the project root folder:
```cmd
Launch-HSCast.bat
```
This automatically sets up all dependencies and opens the **HSCast Studio** control window.


---

## 🖥️ How to Run Using Terminal in Windows

You can run HSCast directly from **PowerShell** or **Command Prompt (CMD)** inside the `windows` directory.

### Step 1: First-Time Setup & System Check
Open your terminal in the `windows` folder and run:
```powershell
cd windows
.\run.ps1 doctor
```
*This verifies your Python version, GPU acceleration, and ADB connection.*

---

### Step 2: Launch via Terminal Commands

#### 1. Open the Graphical Studio (GUI)
```powershell
.\run.ps1
```
*(or `.\run.ps1 gui`)*

---

#### 2. Mirror Phone to PC (Phone ➔ PC)
* **Using USB Cable** *(recommended for lowest latency)*:
  ```bash
  python -m hscast mirror
  ```
  *(or `.\run.ps1 mirror`)*

* **Using Wi-Fi**:
  ```bash
  python -m hscast mirror --wifi <PHONE_IP_ADDRESS>
  ```
  *Example: `python -m hscast mirror --wifi 192.168.1.50`*

---

#### 3. Cast PC Desktop to Phone (PC ➔ Phone)
* **Using USB Cable**:
  ```bash
  python -m hscast desktop
  ```
  *(or `.\run.ps1 desktop`)*

* **Using Wi-Fi**:
  ```bash
  python -m hscast desktop --wifi
  ```
  *(The terminal will display the PC's IP address to enter into your phone app).*

---

## ⌨️ Helpful Keyboard Shortcuts (PC Mirror Window)

| Shortcut | Action |
|---|---|
| `Ctrl + F` | Toggle Fullscreen |
| `Ctrl + B` *(or Right-Click)* | Back button |
| `Ctrl + H` *(or Middle-Click)* | Home screen |
| `Ctrl + S` | Recents / App Switcher |
| `Ctrl + N` | Notification Shade |
| `Ctrl + P` | Lock Screen |
| `Ctrl + W` | Wake Screen |
| `Ctrl + Q` | Quit / Close window |
| `Left-Click & Drag` | Touch swipe / gesture |
| `Mouse Wheel` | Scroll up / down |

---

## 🔧 ADB Configuration & Troubleshooting (USB Issues)

If you encounter connection issues over USB, or if ADB is not installed system-wide, follow these steps:

### 1. How to Add & Enable `adb.exe` in the Windows App (Easiest)
If ADB is not added to your Windows system `PATH`, you can select your `adb.exe` binary directly within the app:

1. Launch **HSCast Studio** (`Launch-HSCast.bat` or `.\run.ps1 gui`).
2. Click the **System Doctor** tab at the top.
3. Locate the **Hardware & Dependency Health Check** card for **Android ADB Bridge**.
4. In the **Custom ADB Binary Path** section below:
   * Click **Browse File...** to open the Windows file picker and select your `adb.exe` (e.g. from `C:\platform-tools\adb.exe` or `C:\Users\<Username>\AppData\Local\Android\Sdk\platform-tools\adb.exe`).
   * *Alternatively*, paste the full file path directly into the text input and click **Apply Path**.
5. The **Android ADB Bridge** status will immediately update to a green **Pass** badge with the confirmed path:
   ```
   Pass — ADB located at: C:\platform-tools\adb.exe
   ```
6. Switch back to the **Cast Studio** tab — your connected Android device will now automatically appear in the device dropdown!

> 💾 **Saved Automatically**: Once selected, your custom ADB path is saved to `.hscast_config.json` and will automatically load on future runs.

---

### 2. Alternative Setup Methods (Command Line & Environment)
* **Add to System PATH**:
  1. Press `Win + R`, type `sysdm.cpl`, and press **Enter**.
  2. Under the **Advanced** tab, click **Environment Variables**.
  3. Under *User* or *System* variables, edit `Path` and add your extracted `platform-tools` folder (e.g., `C:\platform-tools`).
* **Set via PowerShell Terminal**:
  ```powershell
  $env:HSCAST_ADB = "C:\path\to\platform-tools\adb.exe"
  python -m hscast mirror
  ```

---

### 3. Device Not Detected or "Unauthorized"
* Unlock your phone and accept the prompt: **"Allow USB Debugging?"** (check *"Always allow from this computer"* and tap **Allow**).
* Restart the ADB server from terminal:
  ```bash
  adb kill-server
  adb start-server
  adb devices
  ```
* Ensure you are using a **data transfer** USB cable (not charge-only).

---

### 4. Port Stuck or Connection Reset
* Reset active ADB tunnels:
  ```bash
  adb forward --remove-all
  adb reverse --remove-all
  ```

> 💡 **Tip**: If USB/ADB continues to give trouble, switch to **Wi-Fi mode** (`--wifi`). Wi-Fi mode connects directly over your local network and does **not** require ADB or USB cables.

---

## 📋 System Requirements

* **Windows**: Windows 10 or 11 (64-bit), Python 3.12 or newer.
* **Android**: Android 8.0 or higher.
* **Connection**: USB cable with ADB enabled, or both devices on the same Wi-Fi network.

