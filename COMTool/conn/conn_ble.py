'''
    Bluetooth Low Energy (BLE) "virtual serial port" connection.

    Works with transparent-UART BLE modules (CH9143, CH9141, HM-10, JDY-xx,
    Nordic UART Service, ...): the module is connected through the PC's own
    Bluetooth adapter, data written in the app goes to the module's "write"
    characteristic, data notified by the module is shown as received data.

    Needs `bleak` (pip install bleak), works on Windows / Linux(BlueZ) / macOS.
'''
from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (QWidget, QPushButton, QGridLayout, QLabel, QLineEdit,
                             QCheckBox, QComboBox)
try:
    from Combobox import ComboBox
    from i18n import _
except ImportError:
    from COMTool.Combobox import ComboBox
    from COMTool.i18n import _
try:
    from base import COMM, ConnectionStatus
except Exception:
    from .base import COMM, ConnectionStatus
import asyncio, threading, time, re

try:
    import bleak
    from bleak import BleakScanner, BleakClient
    bleakImportError = None
except Exception as e:  # not installed / no backend on this platform
    bleak = None
    bleakImportError = e


def _u16(n):
    return "0000{:04x}-0000-1000-8000-00805f9b34fb".format(n)


# (service, rx(notify from device), tx(write to device)), checked in order
KNOWN_UART_PROFILES = [
    ("6e400001-b5a3-f393-e0a9-e50e24dcca9e",   # Nordic UART Service
     "6e400003-b5a3-f393-e0a9-e50e24dcca9e",
     "6e400002-b5a3-f393-e0a9-e50e24dcca9e"),
    (_u16(0xFFE0), _u16(0xFFE1), _u16(0xFFE1)),  # HM-10 / JDY / many clones
    (_u16(0xFFF0), _u16(0xFFF1), _u16(0xFFF2)),  # WCH CH914x family (common layout)
    ("0000fff0-0000-1000-8000-00805f9b34fb", _u16(0xFFF2), _u16(0xFFF1)),
    ("49535343-fe7d-4ae5-8fa9-9fafd205e455",   # ISSC / Microchip transparent UART
     "49535343-1e4d-4bd9-ba61-23c647249616",
     "49535343-8841-43f4-a8d4-ecbe34729bb3"),
]


class BLE(COMM):
    '''
        call sequence:
            onInit
            onWidget
            onUiInitDone
                isConnected or getConnStatus
                send
            getConfig
            onDel
    '''
    id = "ble"
    name = "Bluetooth LE"
    showSwitchSignal = pyqtSignal(ConnectionStatus)
    scanResultSignal = pyqtSignal(list)
    scanDoneSignal = pyqtSignal()
    infoSignal = pyqtSignal(str)

    def onInit(self, config):
        self.config = config
        default = {
            "device": ["", []],           # [selected text, [history items]]
            "service": "",                # empty: auto detect
            "rx_char": "",
            "tx_char": "",
            "write_with_response": False,
            "auto_reconnect": False,
            "auto_reconnect_interval": 1.0,
            "scan_time": 5.0,
        }
        for k in default:
            if k not in self.config:
                self.config[k] = default[k]
        self.status = ConnectionStatus.CLOSED
        self.widget = None
        self.busy = False
        self.loop = None
        self.loopThread = None
        self.client = None
        self.txQueue = None
        self.txChar = None
        self.txResponse = False
        self.wantConnected = False   # user intent, used by auto reconnect
        self.scanning = False

    # ------------------------------------------------------------------ asyncio loop
    def _ensureLoop(self):
        if self.loop is not None:
            return
        ready = threading.Event()

        def run():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.txQueue = asyncio.Queue()
            ready.set()
            self.loop.run_forever()
            try:
                self.loop.close()
            except Exception:
                pass
        self.loopThread = threading.Thread(target=run, daemon=True)
        self.loopThread.start()
        ready.wait(2)

    def _submit(self, coro):
        self._ensureLoop()
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    # ------------------------------------------------------------------ COMM api
    def getConfig(self):
        return self.config

    def isConnected(self):
        return self.status == ConnectionStatus.CONNECTED

    def getConnStatus(self):
        return self.status

    def disconnect(self):
        if self.status != ConnectionStatus.CLOSED:
            self._startDisconnect()

    def onDel(self):
        self.wantConnected = False
        if self.loop is not None:
            try:
                if self.client is not None:
                    self._submit(self._disconnectAsync()).result(3)
            except Exception:
                pass
            try:
                self.loop.call_soon_threadsafe(self.loop.stop)
            except Exception:
                pass

    def send(self, data: bytes):
        if not self.isConnected() or self.txChar is None:
            return
        self.loop.call_soon_threadsafe(self.txQueue.put_nowait, bytes(data))

    # ------------------------------------------------------------------ UI
    def onWidget(self):
        widget = QWidget()
        layout = QGridLayout()
        self.deviceLabel = QLabel(_("Device"))
        self.deviceCombobox = ComboBox()
        self.deviceCombobox.setEditable(True)
        self.scanButton = QPushButton(_("Scan"))
        self.serviceLabel = QLabel(_("Service"))
        self.serviceEdit = QLineEdit()
        self.serviceEdit.setPlaceholderText(_("auto"))
        self.rxLabel = QLabel("RX (notify)")
        self.rxEdit = QLineEdit()
        self.rxEdit.setPlaceholderText(_("auto"))
        self.txLabel = QLabel("TX (write)")
        self.txEdit = QLineEdit()
        self.txEdit.setPlaceholderText(_("auto"))
        self.withRespLabel = QLabel(_("Write with response"))
        self.withRespCheck = QCheckBox()
        self.autoReconnectLabel = QLabel(_("Auto reconnect"))
        self.autoReconnectCheck = QCheckBox()
        self.autoReconnectIntervalEdit = QLineEdit("1.0")
        self.statusLabel = QLabel("")
        self.statusLabel.setWordWrap(True)
        self.openCloseButton = QPushButton(_("OPEN"))

        layout.addWidget(self.deviceLabel, 0, 0)
        layout.addWidget(self.deviceCombobox, 0, 1, 1, 2)
        layout.addWidget(self.scanButton, 1, 0, 1, 3)
        layout.addWidget(self.serviceLabel, 2, 0)
        layout.addWidget(self.serviceEdit, 2, 1, 1, 2)
        layout.addWidget(self.rxLabel, 3, 0)
        layout.addWidget(self.rxEdit, 3, 1, 1, 2)
        layout.addWidget(self.txLabel, 4, 0)
        layout.addWidget(self.txEdit, 4, 1, 1, 2)
        layout.addWidget(self.withRespLabel, 5, 0)
        layout.addWidget(self.withRespCheck, 5, 1, 1, 2)
        layout.addWidget(self.autoReconnectLabel, 6, 0)
        layout.addWidget(self.autoReconnectCheck, 6, 1)
        layout.addWidget(self.autoReconnectIntervalEdit, 6, 2)
        layout.addWidget(self.statusLabel, 7, 0, 1, 3)
        layout.addWidget(self.openCloseButton, 8, 0, 1, 3)
        widget.setLayout(layout)
        self.widget = widget

        self.openCloseButton.clicked.connect(self.openClose)
        self.scanButton.clicked.connect(self.startScan)
        self.showSwitchSignal.connect(self.showSwitch)
        self.scanResultSignal.connect(self.onScanResult)
        self.scanDoneSignal.connect(self.onScanDone)
        self.infoSignal.connect(self.statusLabel.setText)
        self.deviceCombobox.currentTextChanged.connect(self.onDeviceChanged)
        self.serviceEdit.textChanged.connect(lambda t: self.config.__setitem__("service", t.strip()))
        self.rxEdit.textChanged.connect(lambda t: self.config.__setitem__("rx_char", t.strip()))
        self.txEdit.textChanged.connect(lambda t: self.config.__setitem__("tx_char", t.strip()))
        self.withRespCheck.stateChanged.connect(lambda v: self.config.__setitem__("write_with_response", bool(v)))
        self.autoReconnectCheck.stateChanged.connect(lambda v: self.config.__setitem__("auto_reconnect", bool(v)))
        self.autoReconnectIntervalEdit.textChanged.connect(self.onIntervalChanged)
        return widget

    def onUiInitDone(self):
        for item in self.config["device"][1]:
            self.deviceCombobox.addItem(item)
        self.deviceCombobox.setCurrentText(self.config["device"][0])
        self.serviceEdit.setText(self.config["service"])
        self.rxEdit.setText(self.config["rx_char"])
        self.txEdit.setText(self.config["tx_char"])
        self.withRespCheck.setChecked(self.config["write_with_response"])
        self.autoReconnectCheck.setChecked(self.config["auto_reconnect"])
        self.autoReconnectIntervalEdit.setText("%.3f" % self.config["auto_reconnect_interval"])
        if bleak is None:
            self.infoSignal.emit(_("Python package 'bleak' is not available, install it with: pip install bleak")
                                 + f"\n({bleakImportError})")

    def onDeviceChanged(self, text):
        self.config["device"][0] = text

    def onIntervalChanged(self, text):
        try:
            self.config["auto_reconnect_interval"] = max(0.1, float(text))
        except Exception:
            pass

    def showSwitch(self, status):
        if status == ConnectionStatus.CLOSED:
            self.openCloseButton.setText(_("OPEN"))
            self.openCloseButton.setProperty("class", "")
        elif status == ConnectionStatus.CONNECTED:
            self.openCloseButton.setText(_("CLOSE"))
            self.openCloseButton.setProperty("class", "")
        else:
            self.openCloseButton.setText(_("CLOSE"))
            self.openCloseButton.setProperty("class", "warning")
        self.widget.style().unpolish(self.openCloseButton)
        self.widget.style().polish(self.openCloseButton)
        self.widget.update()

    # ------------------------------------------------------------------ scan
    def _checkBleak(self):
        if bleak is None:
            msg = _("Python package 'bleak' is not available, install it with: pip install bleak") + f"\n({bleakImportError})"
            self.hintSignal.emit("error", _("Error"), msg)
            return False
        return True

    def startScan(self):
        if self.scanning or not self._checkBleak():
            return
        self.scanning = True
        self.scanButton.setEnabled(False)
        self.scanButton.setText(_("Scanning..."))
        self._submit(self._scanAsync())

    async def _scanAsync(self):
        try:
            found = await BleakScanner.discover(timeout=float(self.config["scan_time"]), return_adv=True)
            items = []
            for addr, (dev, adv) in found.items():
                name = dev.name or adv.local_name or ""
                items.append((adv.rssi if adv.rssi is not None else -999, name, dev.address))
            items.sort(reverse=True)
            self.scanResultSignal.emit([self._fmtDevice(n, a, r) for r, n, a in items])
        except Exception as e:
            self.hintSignal.emit("error", _("Error"), _("Scan failed") + "\n" + str(e))
        finally:
            self.scanDoneSignal.emit()

    @staticmethod
    def _fmtDevice(name, addr, rssi):
        return f"{name or '(no name)'} [{addr}] {rssi}dBm"

    def onScanResult(self, items):
        cur = self.deviceCombobox.currentText()
        self.deviceCombobox.clear()
        for item in items:
            self.deviceCombobox.addItem(item)
        self.config["device"][1] = list(items)
        if cur and self._parseAddress(cur) in [self._parseAddress(i) for i in items]:
            self.deviceCombobox.setCurrentText(next(i for i in items if self._parseAddress(i) == self._parseAddress(cur)))
        elif items:
            self.deviceCombobox.setCurrentIndex(0)
        self.infoSignal.emit(_("Found devices:") + f" {len(items)}")

    def onScanDone(self):
        self.scanning = False
        self.scanButton.setEnabled(True)
        self.scanButton.setText(_("Scan"))

    @staticmethod
    def _parseAddress(text):
        '''"Name [AA:BB:..] -60dBm" -> "AA:BB:.." ; plain address is returned as is'''
        text = text.strip()
        m = re.search(r"\[([^\]]+)\]", text)
        return m.group(1).strip() if m else text

    # ------------------------------------------------------------------ open / close
    def openClose(self):
        if self.busy:
            return
        if self.status == ConnectionStatus.CLOSED:
            if not self._checkBleak():
                return
            address = self._parseAddress(self.config["device"][0])
            if not address:
                self.hintSignal.emit("error", _("Error"), _("Please scan and select a device first"))
                return
            self.busy = True
            self.wantConnected = True
            self.status = ConnectionStatus.CONNECTING
            self.onConnectionStatus.emit(self.status, "")
            self.showSwitchSignal.emit(self.status)
            self._submit(self._connectTask(address))
        else:
            self._startDisconnect()

    def _startDisconnect(self):
        self.wantConnected = False
        self.busy = True
        self._submit(self._disconnectTask())

    async def _disconnectTask(self):
        await self._disconnectAsync()
        self.status = ConnectionStatus.CLOSED
        self.onConnectionStatus.emit(self.status, "")
        self.showSwitchSignal.emit(self.status)
        self.infoSignal.emit("")
        self.busy = False

    async def _disconnectAsync(self):
        self.txChar = None
        client, self.client = self.client, None
        if client is not None:
            try:
                await client.disconnect()
            except Exception as e:
                print("-- ble disconnect error:", e)

    # ------------------------------------------------------------------ connect
    async def _connectTask(self, address):
        '''first connect (user pressed OPEN)'''
        try:
            await self._connectOnce(address)
        except Exception as e:
            print("-- ble connect fail:", repr(e))
            await self._disconnectAsync()
            self.wantConnected = False
            self.status = ConnectionStatus.CLOSED
            msg = _("Connect Failed") + "\n" + str(e)
            self.hintSignal.emit("error", _("Error"), msg)
            self.onConnectionStatus.emit(self.status, msg)
            self.showSwitchSignal.emit(self.status)
        finally:
            self.busy = False

    async def _connectOnce(self, address):
        # prefer a fresh scan result: Windows/macOS need it to resolve the device
        device = await BleakScanner.find_device_by_address(address, timeout=float(self.config["scan_time"]))
        if device is None:
            raise Exception(_("Device not found") + f": {address}")
        client = BleakClient(device, disconnected_callback=self._onDisconnected)
        await client.connect()
        try:
            rx, tx = self._pickCharacteristics(client.services)
            await client.start_notify(rx, self._onNotify)
        except Exception:
            try:
                await client.disconnect()
            except Exception:
                pass
            raise
        self.client = client
        self.txChar = tx
        props = tx.properties
        if self.config["write_with_response"] or "write-without-response" not in props:
            self.txResponse = True
        else:
            self.txResponse = False
        # drop stale tx data from a previous session
        while not self.txQueue.empty():
            self.txQueue.get_nowait()
        if not getattr(self, "_writerTask", None) or self._writerTask.done():
            self._writerTask = self.loop.create_task(self._writerLoop())
        self.status = ConnectionStatus.CONNECTED
        self.infoSignal.emit(f"RX {rx.uuid}\nTX {tx.uuid}")
        self.onConnectionStatus.emit(self.status, "")
        self.showSwitchSignal.emit(self.status)

    def _pickCharacteristics(self, services):
        '''return (rx_char, tx_char) objects, user config first, then known profiles, then guess'''
        chars = {}
        for s in services:
            for c in s.characteristics:
                chars[c.uuid.lower()] = (s, c)

        def norm(u):
            u = u.strip().lower()
            if u.startswith("0x"):
                u = u[2:]
            if re.fullmatch(r"[0-9a-f]{4}", u):
                u = _u16(int(u, 16))
            return u

        def canNotify(c):
            return "notify" in c.properties or "indicate" in c.properties

        def canWrite(c):
            return "write" in c.properties or "write-without-response" in c.properties

        cfgRx, cfgTx, cfgSvc = norm(self.config["rx_char"]), norm(self.config["tx_char"]), norm(self.config["service"])
        if cfgRx or cfgTx:
            rx = chars.get(cfgRx, (None, None))[1] if cfgRx else None
            tx = chars.get(cfgTx, (None, None))[1] if cfgTx else None
            if cfgRx and rx is None:
                raise Exception(_("RX characteristic not found") + f": {cfgRx}")
            if cfgTx and tx is None:
                raise Exception(_("TX characteristic not found") + f": {cfgTx}")
            # only one given: the other may be the same char (e.g. FFE1) or auto
            if rx is None:
                rx = tx if canNotify(tx) else None
            if tx is None:
                tx = rx if canWrite(rx) else None
            if rx is None or tx is None:
                raise Exception(_("RX and TX characteristics are both needed"))
            return rx, tx

        for svc, rxu, txu in KNOWN_UART_PROFILES:
            if cfgSvc and svc != cfgSvc:
                continue
            if rxu in chars and txu in chars and canNotify(chars[rxu][1]) and canWrite(chars[txu][1]):
                return chars[rxu][1], chars[txu][1]

        # guess: inside the (given or first suitable) vendor service
        generic = ("00001800", "00001801", "0000180a", "0000180f")  # GAP/GATT/DIS/battery
        for s in services:
            if cfgSvc and s.uuid.lower() != cfgSvc:
                continue
            if s.uuid.lower().startswith(generic):
                continue
            rx = next((c for c in s.characteristics if canNotify(c)), None)
            tx = next((c for c in s.characteristics if canWrite(c) and "write-without-response" in c.properties), None) \
                 or next((c for c in s.characteristics if canWrite(c)), None)
            if rx and tx:
                return rx, tx
        raise Exception(_("No UART-like service found, set Service / RX / TX UUID manually"))

    # ------------------------------------------------------------------ data path
    def _onNotify(self, sender, data: bytearray):
        try:
            self.onReceived(bytes(data))
        except Exception as e:
            print("-- error in onReceived callback:", e)

    async def _writerLoop(self):
        while True:
            data = await self.txQueue.get()
            client, char = self.client, self.txChar
            if client is None or char is None:
                continue
            try:
                mtu = getattr(client, "mtu_size", 23) or 23
                chunk = max(20, mtu - 3)
                for i in range(0, len(data), chunk):
                    await client.write_gatt_char(char, data[i:i + chunk], response=self.txResponse)
            except Exception as e:
                print("-- ble write error:", repr(e))
                self.hintSignal.emit("error", _("Error"), _("Send failed") + "\n" + str(e))

    # ------------------------------------------------------------------ lost connection
    def _onDisconnected(self, client):
        # called by bleak in loop thread
        if client is not self.client:
            return
        self.client = None
        self.txChar = None
        if not self.wantConnected or self.status == ConnectionStatus.CLOSED:
            return
        if self.config["auto_reconnect"]:
            self.status = ConnectionStatus.LOSE
            self.onConnectionStatus.emit(self.status, _("Connection lose!"))
            self.showSwitchSignal.emit(self.status)
            asyncio.run_coroutine_threadsafe(self._reconnectLoop(), self.loop)
        else:
            self.wantConnected = False
            self.status = ConnectionStatus.CLOSED
            self.onConnectionStatus.emit(self.status, _("Connection closed!"))
            self.showSwitchSignal.emit(self.status)

    async def _reconnectLoop(self):
        address = self._parseAddress(self.config["device"][0])
        while self.wantConnected and self.status == ConnectionStatus.LOSE:
            await asyncio.sleep(self.config["auto_reconnect_interval"])
            if not self.wantConnected:
                break
            try:
                self.onConnectionStatus.emit(ConnectionStatus.CONNECTING, "")
                self.status = ConnectionStatus.CONNECTING
                await self._connectOnce(address)
                self.onConnectionStatus.emit(ConnectionStatus.CONNECTED, _("Reconnected"))
                return
            except Exception as e:
                print("-- ble reconnect fail:", repr(e))
                self.status = ConnectionStatus.LOSE
                self.onConnectionStatus.emit(self.status, _("Connection lose!"))
