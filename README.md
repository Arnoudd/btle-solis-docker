# btle-solis

Dockerized interface for reading data from a supported Solis inverter over Bluetooth Low Energy (BLE) and publishing the data to MQTT.

This project is based on the original [cryptocake/btle-solis](https://github.com/cryptocake/btle-solis) project. The original Solis BLE protocol, register definitions and MQTT data structure are retained. This version adds a Docker/Compose deployment, environment-based configuration and structured logging.

The original project was written for the Zonneplan Nexus Home battery with a Solis S6 EH3P10K-H-EU(ZP) inverter. Hardware compatibility outside the tested setup is not guaranteed.

> **Disclaimer:** This project is not officially associated with Zonneplan or Solis. Use it at your own risk.

## Features

- Reads Solis inverter data over Bluetooth Low Energy.
- Publishes the collected data as JSON to MQTT.
- Supports `LITE_MODE` for a smaller set of registers.
- Runs as a Docker container with Docker Compose.
- Configuration is kept in `compose.yaml`.
- Structured logging with configurable `LOG_LEVEL`.
- Automatically restarts after failures or host reboots.
- The same Compose file can be deployed with standard Docker Compose or Compose-compatible tools such as Portainer and Dockhand.

## Requirements

- Raspberry Pi or Linux host with a Bluetooth adapter supported by BlueZ.
- Docker Engine with Docker Compose support.
- A supported Solis inverter within Bluetooth range.
- An MQTT broker.
- Home Assistant if you want to consume the MQTT data as sensors.

The project has been tested on a Raspberry Pi 4 with onboard Bluetooth.

## 1. Prepare Bluetooth on the Raspberry Pi

Install BlueZ and make sure the Bluetooth service is running:

```bash
sudo apt update
sudo apt install -y bluez bluetooth
sudo systemctl enable bluetooth
sudo systemctl start bluetooth
```

Check that the Bluetooth adapter is available:

```bash
hciconfig -a
```

You should see an adapter such as `hci0` with `UP RUNNING`.

You can also check it with:

```bash
bluetoothctl list
```

## 2. Find and test the Solis Bluetooth device

Start the Bluetooth command line tool:

```bash
bluetoothctl
```

Then:

```text
power on
agent on
scan on
```

The Solis inverter normally appears with a Bluetooth name beginning with `INV_`.

Take note of its MAC address.

The original project recommends trusting the device and testing a connection before running the application:

```text
trust XX:XX:XX:XX:XX:XX
connect XX:XX:XX:XX:XX:XX
```

If the connection fails, press and hold the touch button on the inverter for approximately 5 seconds until it starts flashing, then try again.

After a successful test, disconnect it:

```text
disconnect XX:XX:XX:XX:XX:XX
```

Exit `bluetoothctl` with:

```text
exit
```

## 3. Install Docker

If Docker is not already installed, install Docker using the official Docker instructions for your Raspberry Pi/Linux distribution.

Verify the installation:

```bash
docker --version
docker compose version
```

The project does not require Portainer, Dockhand, or any other management interface. They are optional ways of deploying the same Compose configuration.

## 4. Clone the repository

```bash
git clone https://github.com/Arnoudd/btle-solis.git
cd btle-solis
```

If you are using your own fork, replace the repository URL accordingly.

## 5. Configure `compose.yaml`

All application configuration is intentionally kept in `compose.yaml`.

Edit it:

```bash
nano compose.yaml
```

At minimum, change:

```yaml
SOLIS_MAC_ADDRESS: "XX:XX:XX:XX:XX:XX"
MQTT_BROKER: "192.168.1.100" 
MQTT_PORT: "1883" 
MQTT_USERNAME: "YOUR_MQTT_USERNAME" # Leave empty if not needed for our MQTT configuration
MQTT_PASSWORD: "YOUR_MQTT_PASSWORD" # Leave empty if not needed for our MQTT configuration
```

If your MQTT broker does not require authentication, leave `MQTT_USERNAME` and `MQTT_PASSWORD` empty.

### Configuration options

| Variable | Description | Default |
|---|---|---|
| `SOLIS_MAC_ADDRESS` | Bluetooth MAC address of the Solis inverter | required |
| `MQTT_BROKER` | MQTT broker hostname or IP address | required |
| `MQTT_PORT` | MQTT broker port | `1883` |
| `MQTT_USERNAME` | MQTT username | empty |
| `MQTT_PASSWORD` | MQTT password | empty |
| `INTERVAL` | Seconds between polling cycles | `15` |
| `TEST_MODE` | Read data but do not publish to MQTT | `false` |
| `LITE_MODE` | Read only the smaller register set | `true` |
| `LOG_LEVEL` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) | `INFO` |

### MQTT credentials

Replace the placeholder values in your local `compose.yaml` before starting the container.

If you are maintaining your own private fork/repository, keeping the values directly in `compose.yaml` is supported by design.

## 6. Deploy with Docker Compose

Build the image and start the container:

```bash
docker compose up -d --build
```

Check that it is running:

```bash
docker compose ps
```

## 7. Deploy with Portainer or Dockhand

The repository contains a standard Compose file. You do **not** need Portainer or Dockhand to use this project, but the same `compose.yaml` can be deployed through Docker management tools that support Docker Compose stacks.

### Portainer

In Portainer, create a new **Stack** and deploy it from the Git repository, or paste/upload the contents of `compose.yaml`.

If deploying from Git, select the repository and set the Compose file path to:

```text
compose.yaml
```

Edit the values in the `environment:` section before deploying, especially:

- `SOLIS_MAC_ADDRESS`
- `MQTT_BROKER`
- `MQTT_PORT`
- `MQTT_USERNAME`
- `MQTT_PASSWORD`

The target Docker endpoint must be the Linux/Raspberry Pi host that has the Bluetooth adapter. Running the container on a different Docker host will not give it access to the Raspberry Pi's Bluetooth hardware.

### Dockhand

Dockhand can likewise deploy the repository as a Docker Compose project. Point the project at the repository and use:

```text
compose.yaml
```

Make sure the project is deployed to the Docker host that physically has the Bluetooth adapter connected.

### Important: Bluetooth is host-specific

This application uses:

```yaml
network_mode: host
privileged: true
```

and mounts the host D-Bus socket. These settings are intentional because the application needs direct access to the host Bluetooth adapter.

A Portainer or Dockhand server running on another machine can manage the Raspberry Pi remotely, but the **container itself must run on the Raspberry Pi (or another host with the required Bluetooth adapter)**.

## 8. View the logs

Follow the application logs:

```bash
docker compose logs -f
```

With the default `LOG_LEVEL=INFO`, the logs show connection status, register blocks, MQTT publishing and errors.

For detailed BLE troubleshooting, temporarily change:

```yaml
LOG_LEVEL: "DEBUG"
```

Then recreate the container:

```bash
docker compose up -d --build
```

## 9. MQTT output

The application publishes the inverter data to:

```text
homeassistant/btle-solis/data
```

The payload is a JSON object containing the values from the configured register map.

For example, in `LITE_MODE` the application reads the configured register blocks from `REGISTERS_LITE` and publishes their resulting values.

## 10. Home Assistant

Home Assistant can consume the MQTT topic and expose the values as sensors.

The original `cryptocake/btle-solis` project contains Home Assistant MQTT sensor definitions (`mqtt.yaml` and `mqtt-lite.yaml`). These are Home Assistant configuration files, not Docker configuration, and therefore are intentionally not required by the container.

Use the corresponding Home Assistant MQTT configuration from the original project or adapt it to your own setup.

Original project:

https://github.com/cryptocake/btle-solis

## 11. Updating

Pull the latest repository version and rebuild the container:

```bash
git pull
docker compose up -d --build
```

When using Portainer, Dockhand, or another Compose management tool, use its normal redeploy/update function after pulling the new repository version.

## 12. Stopping and starting

Stop the container:

```bash
docker compose down
```

Start it again:

```bash
docker compose up -d
```

## 13. Troubleshooting

### `hci0` is missing

Check the host first:

```bash
hciconfig -a
```

Make sure Bluetooth is enabled:

```bash
sudo systemctl status bluetooth
```

### `hci0` exists on the host but Bluetooth does not work in the container

Check from inside the container:

```bash
docker exec -it btle-solis hciconfig -a
```

The container should also see `hci0`.

The Compose configuration uses `privileged: true` and host networking because `bluepy` requires direct access to the host Bluetooth adapter.

### `Failed to connect to peripheral`

First make sure that no other process is using the Solis Bluetooth connection.

For example, check whether an old systemd installation is still running:

```bash
systemctl status btle-solis --no-pager
```

If you previously installed the original systemd version, stop and disable it:

```bash
sudo systemctl disable --now btle-solis
```

Then restart the Docker container:

```bash
docker compose restart
```

You can also test Bluetooth scanning from inside the container:

```bash
docker exec -it btle-solis python -c "from bluepy.btle import Scanner; print(Scanner().scan(10))"
```

### MQTT connection problems

Check the broker settings in `compose.yaml` and inspect the logs:

```bash
docker compose logs -f
```

The application logs MQTT connection and publish errors without logging the MQTT password.

### No MQTT messages

Verify that the container is running:

```bash
docker compose ps
```

Then inspect the logs:

```bash
docker compose logs -f
```

The expected MQTT topic is:

```text
homeassistant/btle-solis/data
```

## 14. LITE_MODE

With:

```yaml
LITE_MODE: "true"
```

the application reads only the smaller register set defined by `REGISTERS_LITE`.

With:

```yaml
LITE_MODE: "false"
```

the full register map is used.

LITE_MODE was introduced by the original project for installations where other battery information is already retrieved through another protocol.

## 15. Logging

The application uses Python's standard logging framework.

Normal operation:

```yaml
LOG_LEVEL: "INFO"
```

Detailed troubleshooting:

```yaml
LOG_LEVEL: "DEBUG"
```

The logs include timestamps and log levels, making them suitable for `docker compose logs` and normal Docker logging systems.

## Credits

This project is based on:

**cryptocake/btle-solis**

https://github.com/cryptocake/btle-solis

The original project provides the Solis BLE protocol implementation, register definitions and Home Assistant MQTT configuration. This Dockerized version adds the container deployment, Compose configuration, environment-based settings and extended logging.

The original project is licensed under the MIT License. See `LICENSE` for the original copyright and license text.
