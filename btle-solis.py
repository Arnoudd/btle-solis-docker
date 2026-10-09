import os
import logging
import time
import json
import struct
import queue

from bluepy.btle import (
    UUID,
    Peripheral,
    DefaultDelegate,
    BTLEException,
    BTLEDisconnectError,
)

import paho.mqtt.client as mqtt
import registers


# =============================================================================
# Logging
# =============================================================================

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger("btle-solis")


# =============================================================================
# Configuration via environment variables (Portainer)
# =============================================================================

MAC_ADDRESS = os.getenv("SOLIS_MAC_ADDRESS", "")

SERVICE_UUID_FFE0 = "0000ffe0-0000-1000-8000-00805f9b34fb"
CHAR_UUID_FFE1 = "0000ffe1-0000-1000-8000-00805f9b34fb"
CHAR_UUID_FFE2 = "0000ffe2-0000-1000-8000-00805f9b34fb"

MQTT_BROKER_IP = os.getenv("MQTT_BROKER", "")
MQTT_BROKER_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USERNAME = os.getenv("MQTT_USERNAME", "")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD", "")
MQTT_COMMAND_TOPIC = os.getenv(
    "MQTT_COMMAND_TOPIC",
    "home/btle-solis/command",
)
MQTT_RESULT_TOPIC = os.getenv(
    "MQTT_RESULT_TOPIC",
    "home/btle-solis/command/result",
)

INTERVAL = int(os.getenv("INTERVAL", "15"))
TEST_MODE = os.getenv("TEST_MODE", "false").lower() == "true"
LITE_MODE = os.getenv("LITE_MODE", "true").lower() == "true"


# Select register map and register blocks based on LITE_MODE.
if LITE_MODE:
    REGISTER_MAP = registers.REGISTER_MAP_LITE
    REGISTERS = registers.REGISTERS_LITE
else:
    REGISTER_MAP = registers.REGISTER_MAP
    REGISTERS = registers.REGISTERS


# =============================================================================
# Startup information
# =============================================================================

logger.info("Starting btle-solis")
logger.info("Bluetooth MAC address: %s", MAC_ADDRESS)
logger.info("MQTT broker: %s:%s", MQTT_BROKER_IP, MQTT_BROKER_PORT)
logger.info("MQTT command topic: %s", MQTT_COMMAND_TOPIC)
logger.info("MQTT result topic: %s", MQTT_RESULT_TOPIC)
logger.info("Interval: %s seconds", INTERVAL)
logger.info("LITE_MODE: %s", LITE_MODE)
logger.info("TEST_MODE: %s", TEST_MODE)
logger.info("Register blocks: %s", len(REGISTERS))


# =============================================================================
# Bluetooth notification delegate
# =============================================================================

class NotificationDelegate(DefaultDelegate):
    def __init__(self):
        DefaultDelegate.__init__(self)
        self.response = b""
        self.done = False

    def handleNotification(self, cHandle, data):
        self.response += data

        logger.debug(
            "BLE notification received: handle=%s bytes=%s",
            cHandle,
            len(data),
        )

        if len(self.response) >= 2:
            received_crc = self.response[-2:]
            calculated_crc = calculate_checksum(self.response[:-2].hex())

            if received_crc.hex().upper() == calculated_crc.upper():
                self.done = True
                logger.debug(
                    "BLE response CRC valid (%s)",
                    received_crc.hex().upper(),
                )
            else:
                self.done = False
                logger.debug(
                    "BLE response CRC not valid: received=%s calculated=%s",
                    received_crc.hex().upper(),
                    calculated_crc.upper(),
                )


# =============================================================================
# Solis protocol helpers
# =============================================================================

def calculate_checksum(data):
    data_bytes = bytearray.fromhex(data)
    crc = 0xFFFF

    for byte in data_bytes:
        crc ^= byte

        for _ in range(8):
            if crc & 1:
                crc >>= 1
                crc ^= 0xA001
            else:
                crc >>= 1

    crc_high = (crc & 0xFF00) >> 8
    crc_low = crc & 0x00FF

    crc_hex = f"{crc_low:02X}{crc_high:02X}"

    return crc_hex


def construct_command(register_address: int, amount, func_code):
    command = f"FE{func_code}{register_address:04X}{amount:04X}"
    crc_hex = calculate_checksum(command)

    result = f"{command}{crc_hex}"

    logger.debug(
        "Constructed command: register=%s length=%s function=%s command=%s",
        register_address,
        amount,
        func_code,
        result,
    )

    return result


def construct_write_command(register_address: int, value: int):
    if not 0 <= register_address <= 0xFFFF:
        raise ValueError("Register address must be between 0 and 65535")

    if not 0 <= value <= 0xFFFF:
        raise ValueError("Register value must be between 0 and 65535")

    command = f"FE06{register_address:04X}{value:04X}"
    crc_hex = calculate_checksum(command)

    result = f"{command}{crc_hex}"

    logger.debug(
        "Constructed write command: register=%s value=%s command=%s",
        register_address,
        value,
        result,
    )

    return result


def parse_response(response, address, length):
    byte_count = response[2]

    data_start = 3
    data_end = data_start + byte_count

    data_field = response[data_start:data_end]

    logger.debug(
        "Parsing response: register=%s bytes=%s length=%s",
        address,
        byte_count,
        length,
    )

    i = 0

    while i < len(data_field):
        current_address = address + i // 2

        if i // 2 < length and current_address in REGISTER_MAP:
            num = REGISTER_MAP[current_address].get("num", 1)
            val = 0

            # Combine the bytes to form the full value
            for j in range(num):
                if i + 2 * j < len(data_field):
                    val = (
                        val << 16
                    ) | struct.unpack(
                        ">H",
                        data_field[
                            i + 2 * j:i + 2 * j + 2
                        ],
                    )[0]

            # Check if the value is signed
            if REGISTER_MAP[current_address].get("negative", 0) == 1:
                max_value = 1 << (16 * num)

                if val >= max_value // 2:
                    val -= max_value

            gain = REGISTER_MAP[current_address].get("gain", 1)

            calculated_value = (
                val * gain if gain is not None else val
            )

            if (
                isinstance(calculated_value, float)
                and calculated_value != int(calculated_value)
            ):
                calculated_value = round(calculated_value, 2)

            REGISTER_MAP[current_address]["value"] = calculated_value

            logger.debug(
                "Register %s: %s=%s %s",
                current_address,
                REGISTER_MAP[current_address].get("name", "unknown"),
                calculated_value,
                REGISTER_MAP[current_address].get("unit", ""),
            )

            i += 2 * num

        else:
            i += 2


# =============================================================================
# MQTT
# =============================================================================

def configure_mqtt_client(client):
    if MQTT_USERNAME != "" and MQTT_PASSWORD != "":
        client.username_pw_set(
            MQTT_USERNAME,
            MQTT_PASSWORD,
        )
        logger.debug("MQTT authentication enabled")
    else:
        logger.debug("MQTT authentication disabled")


def publish_data_to_mqtt():
    logger.info(
        "Publishing data to MQTT: %s:%s",
        MQTT_BROKER_IP,
        MQTT_BROKER_PORT,
    )

    try:
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2
        )

        configure_mqtt_client(client)

        logger.debug("Connecting to MQTT broker")

        client.connect(
            MQTT_BROKER_IP,
            MQTT_BROKER_PORT,
            60,
        )

        payload = json.dumps(REGISTER_MAP)

        result = client.publish(
            "home/btle-solis/data",
            payload,
            retain=True,
        )

        result.wait_for_publish()

        logger.info(
            "MQTT publish successful: topic=home/btle-solis/data payload=%s bytes",
            len(payload),
        )

        client.disconnect()

        logger.debug("MQTT disconnected")

    except Exception:
        logger.exception("MQTT publish failed")
        raise


# =============================================================================
# Bluetooth Solis interface
# =============================================================================

class BtleSolis:

    def __init__(self):
        self.peripheral = None
        self.delegate = None
        self.char_ffe1_handle = None
        self.char_ffe2_handle = None

        self.command_queue = queue.Queue()
        self.command_mqtt_client = None

        self.cycle = 0

    def setup_command_mqtt(self):
        if not MQTT_BROKER_IP:
            raise ValueError("MQTT_BROKER is required for MQTT commands")

        self.command_mqtt_client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2
        )
        configure_mqtt_client(self.command_mqtt_client)

        self.command_mqtt_client.on_connect = self.on_command_mqtt_connect
        self.command_mqtt_client.on_message = self.on_command_mqtt_message

        logger.info(
            "Connecting command MQTT client to %s:%s",
            MQTT_BROKER_IP,
            MQTT_BROKER_PORT,
        )

        self.command_mqtt_client.connect(
            MQTT_BROKER_IP,
            MQTT_BROKER_PORT,
            60,
        )
        self.command_mqtt_client.loop_start()

    def stop_command_mqtt(self):
        if self.command_mqtt_client is not None:
            logger.info("Stopping command MQTT client")

            try:
                self.command_mqtt_client.loop_stop()
                self.command_mqtt_client.disconnect()
            except Exception:
                logger.exception("Error while stopping command MQTT client")

            self.command_mqtt_client = None

    def on_command_mqtt_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            logger.error(
                "Command MQTT connection failed: %s",
                reason_code,
            )
            return

        client.subscribe(MQTT_COMMAND_TOPIC)

        logger.info(
            "Subscribed to MQTT command topic: %s",
            MQTT_COMMAND_TOPIC,
        )

    def on_command_mqtt_message(self, client, userdata, message):
        try:
            payload = json.loads(message.payload.decode("utf-8"))

            if not isinstance(payload, dict):
                raise ValueError("Command payload must be a JSON object")

            register = payload.get("register")
            value = payload.get("value")

            if isinstance(register, bool) or not isinstance(register, int):
                raise ValueError("register must be an integer")

            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError("value must be an integer")

            self.command_queue.put(
                {
                    "register": register,
                    "value": value,
                }
            )

            logger.info(
                "MQTT write command queued: register=%s value=%s",
                register,
                value,
            )

        except Exception as exc:
            logger.error(
                "Invalid MQTT write command: %s",
                exc,
            )
            self.publish_command_result(
                {
                    "status": "error",
                    "error": str(exc),
                }
            )

    def publish_command_result(self, result):
        if self.command_mqtt_client is None:
            logger.error(
                "Cannot publish command result: MQTT client is not available"
            )
            return

        try:
            payload = json.dumps(result)

            mqtt_result = self.command_mqtt_client.publish(
                MQTT_RESULT_TOPIC,
                payload,
                retain=False,
            )

            mqtt_result.wait_for_publish()

            logger.info(
                "MQTT command result published: topic=%s payload=%s",
                MQTT_RESULT_TOPIC,
                payload,
            )

        except Exception:
            logger.exception("Failed to publish MQTT command result")

    def process_command_queue(self):
        while True:
            try:
                command = self.command_queue.get_nowait()
            except queue.Empty:
                return

            try:
                self.process_write_command(
                    command["register"],
                    command["value"],
                )
            except Exception as exc:
                logger.exception(
                    "MQTT write command failed: register=%s value=%s",
                    command.get("register"),
                    command.get("value"),
                )

                self.publish_command_result(
                    {
                        "status": "error",
                        "register": command.get("register"),
                        "raw_value": command.get("value"),
                        "error": str(exc),
                    }
                )
            finally:
                self.command_queue.task_done()

    def process_write_command(self, register_address, raw_value):
        register = registers.WRITE_REGISTERS.get(register_address)

        if register is None:
            raise ValueError(
                f"Register {register_address} is not writable"
            )

        min_raw = register.get("min_raw")
        max_raw = register.get("max_raw")

        if min_raw is not None and raw_value < min_raw:
            raise ValueError(
                f"Value {raw_value} is below minimum {min_raw}"
            )

        if max_raw is not None and raw_value > max_raw:
            raise ValueError(
                f"Value {raw_value} is above maximum {max_raw}"
            )

        command = construct_write_command(
            register_address,
            raw_value,
        )

        response = self.send_command_and_get_response(command)

        expected = bytes.fromhex(
            f"FE06{register_address:04X}{raw_value:04X}"
        )

        if response[:-2] != expected:
            raise ValueError(
                "Unexpected FC06 response: "
                f"{response.hex().upper()}"
            )

        gain = register.get("gain", 1)
        calculated_value = (
            raw_value * gain
            if gain is not None
            else raw_value
        )

        if (
            isinstance(calculated_value, float)
            and calculated_value != int(calculated_value)
        ):
            calculated_value = round(calculated_value, 2)

        result = {
            "status": "success",
            "register": register_address,
            "name": register.get("name", "unknown"),
            "raw_value": raw_value,
            "value": calculated_value,
            "unit": register.get("unit", ""),
        }

        logger.info(
            "MQTT register write successful: register=%s name=%s raw_value=%s value=%s %s",
            register_address,
            register.get("name", "unknown"),
            raw_value,
            calculated_value,
            register.get("unit", ""),
        )

        self.publish_command_result(result)

    def connect(self):
        logger.info(
            "Connecting to Solis inverter %s",
            MAC_ADDRESS,
        )

        try:
            self.peripheral = Peripheral(MAC_ADDRESS)

            self.delegate = NotificationDelegate()

            self.peripheral.setDelegate(
                self.delegate
            )

            logger.info("Bluetooth connection established")

            service_ffe0 = (
                self.peripheral.getServiceByUUID(
                    UUID(SERVICE_UUID_FFE0)
                )
            )

            self.char_ffe1_handle = (
                service_ffe0.getCharacteristics(
                    UUID(CHAR_UUID_FFE1)
                )[0]
            )

            self.char_ffe2_handle = (
                service_ffe0.getCharacteristics(
                    UUID(CHAR_UUID_FFE2)
                )[0]
            )

            logger.debug(
                "BLE characteristics initialized: FFE1=%s FFE2=%s",
                self.char_ffe1_handle.valHandle,
                self.char_ffe2_handle.valHandle,
            )

        except Exception:
            logger.exception(
                "Failed to connect to Solis inverter"
            )
            raise

    def disconnect(self):
        if self.peripheral is not None:
            logger.info("Disconnecting from Solis inverter")

            try:
                self.peripheral.disconnect()
            except Exception:
                logger.exception(
                    "Error while disconnecting from Solis"
                )

            self.peripheral = None

    def send_command_and_get_response(
        self,
        command,
        timeout=5,
    ):
        logger.debug(
            "Sending BLE command: %s",
            command,
        )

        command = bytearray.fromhex(command)

        self.char_ffe1_handle.write(
            command,
            withResponse=False,
        )

        self.peripheral.writeCharacteristic(
            self.char_ffe2_handle.valHandle + 1,
            b"\x01\x00",
            withResponse=True,
        )

        self.delegate.response = b""
        self.delegate.done = False

        start_time = time.time()

        while not self.delegate.done:

            if time.time() - start_time > timeout:
                raise TimeoutError(
                    f"Timeout({timeout}) occurred while waiting on data."
                )

            self.peripheral.waitForNotifications(
                timeout
            )

        elapsed = time.time() - start_time

        logger.debug(
            "BLE response received: %s bytes in %.2fs",
            len(self.delegate.response),
            elapsed,
        )

        return self.delegate.response

    def run(self):
        self.cycle += 1

        cycle_start = time.time()

        logger.info(
            "Starting data cycle #%s",
            self.cycle,
        )

        success_count = 0
        error_count = 0

        for reg, info in REGISTERS.items():

            length = info["length"]
            func_code = info["func_code"]

            command = construct_command(
                int(reg),
                length,
                func_code,
            )

            logger.debug(
                "Reading register block %s (length=%s, function=%s)",
                reg,
                length,
                func_code,
            )

            try:
                response = (
                    self.send_command_and_get_response(
                        command
                    )
                )

                parse_response(
                    response,
                    int(reg),
                    length,
                )

                success_count += 1

                logger.debug(
                    "Register block %s read successfully",
                    reg,
                )

            except BTLEException as e:
                # A BTLEException can mean bluepy's helper process or the
                # peripheral connection has stopped. Continuing to the next
                # register leaves the same dead helper in use and can result
                # in a stream of errors plus stale MQTT data. Let loop()
                # disconnect and establish a fresh BLE session instead.
                error_count += 1

                logger.error(
                    "BLE failure while retrieving register %s: %s. "
                    "Aborting this data cycle and reconnecting.",
                    reg,
                    e,
                )
                raise

            except Exception as e:
                error_count += 1

                logger.error(
                    "Error retrieving data for register %s: %s",
                    reg,
                    e,
                )

                continue

        self.process_command_queue()

        if TEST_MODE:
            logger.info(
                "TEST_MODE enabled - MQTT publish skipped"
            )

        else:
            publish_data_to_mqtt()

        cycle_time = time.time() - cycle_start

        logger.info(
            "Data cycle #%s completed in %.2fs: %s successful, %s errors",
            self.cycle,
            cycle_time,
            success_count,
            error_count,
        )

    def loop(self):

        logger.info("Entering continuous operation")

        self.setup_command_mqtt()

        while True:

            try:
                self.connect()

                while True:
                    self.run()

                    logger.debug(
                        "Waiting %s seconds before next cycle",
                        INTERVAL,
                    )

                    time.sleep(INTERVAL)

            except BTLEDisconnectError as e:

                logger.error(
                    "Bluetooth disconnected: %s",
                    e,
                )

                logger.info(
                    "Retrying in %s seconds",
                    INTERVAL,
                )

            except BTLEException as e:

                logger.error(
                    "Bluetooth error: %s",
                    e,
                )

                logger.info(
                    "Retrying in %s seconds",
                    INTERVAL,
                )

            except Exception as e:

                logger.exception(
                    "Unexpected error: %s",
                    e,
                )

                logger.info(
                    "Retrying in %s seconds",
                    INTERVAL,
                )

            finally:

                self.disconnect()

                time.sleep(INTERVAL)


# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":

    logger.info("btle-solis initialization complete")

    if TEST_MODE:

        logger.info(
            "Running in TEST_MODE"
        )

        bs = BtleSolis()

        try:
            bs.connect()
            bs.run()
        finally:
            bs.disconnect()

    else:

        BtleSolis().loop()
