import minimalmodbus
from time import sleep
from datetime import datetime as dt
from datetime import timedelta
import logging
import sys
import argparse

MINIMUM_SWITCH_TIME = 60  # shortest allowed time between boiler switching actions


class WaterHeater:
    def __init__(self, instrument: minimalmodbus.Instrument):
        self._dbusservice = []
        self.instrument = instrument

        self.registers = {
            "Power_500W": 0,
            "Power_1000W": 1,
            "Power_2000W": 2,
            "Temperature": 0,
            "Heartbeat_Return": 1,
            "Power_Return": 2,
            "Device_Type": 3,
            "Operation_Mode": 4,  # AUTO/FORCE ON
            "Heartbeat": 0,
        }

        self.powersteps = [
            (-1000000, 499),
            (500, 999),
            (1000, 1499),
            (1500, 1999),
            (2000, 2499),
            (2500, 2999),
            (3000, 3499),
            (3500, 1000000),
        ]
        self.powercommands = [
            [0, 0, 0],
            [1, 0, 0],
            [0, 1, 0],
            [1, 1, 0],
            [0, 0, 1],
            [1, 0, 1],
            [0, 1, 1],
            [1, 1, 1],
        ]
        self.lasttime_switched = dt.now() - timedelta(seconds=MINIMUM_SWITCH_TIME)
        self.target_temperature = 50  # °C
        self.current_temperature = float()
        self.current_power = int()
        self.status = None  # 0 Auto, 1 FORCE ON
        self.heartbeat = 0
        self.Device_Type = 0xE5E1
        self.exception_counter = 0
        self.Max_Retries = 10
        self.last_grid_surplus = 0
        self.cmd_bits = [0, 0, 0]
        self.connected = False

    def check_device_type(self):
        maxtries = 3
        tried = 0
        found_type = 0
        for _ in range(maxtries):
            try:
                tried += 1
                found_type = self.instrument.read_register(
                    self.registers["Device_Type"], 0, 4
                )
            except Exception as e:
                logging.warning(f"Water Heater check type: {str(e)})")
                if tried >= maxtries:
                    raise e
                sleep(1)
                continue

            if found_type == self.Device_Type:
                logging.info(f"Found Water Heater (type: {found_type:X})")
                self.connected = True
                return
        raise RuntimeError("No Device found")

    def calc_powercmd(self, grid_surplus):
        res = None
        for idx in (
            idx
            for idx, (sec, fir) in enumerate(self.powersteps)
            if sec <= grid_surplus <= fir
        ):
            res = idx
        return self.powercommands[res]

    def operate(self, grid_surplus):
        # needs to be called regularly (e.g. 1/s) to update the heartbeat

        if self.connected is not True:
            return

        try:
            self.instrument.write_register(
                self.registers["Heartbeat"], self.heartbeat, 0, 16
            )
            self.heartbeat += 1
            if self.heartbeat >= 100:  # must be below 1000 for the server to work
                self.heartbeat = 0

            # switch to apropriate power level, if last switching incident is longer than the allowed minimum time ago
            # short delay for small steps, long delay for steps>500W, immediately switch for downsteps
            powerstep = grid_surplus - self.last_grid_surplus
            if powerstep < 0:
                self.cmd_bits = self.calc_powercmd(
                    grid_surplus
                )  # calculate power setting depending on energy surplus
                self.lasttime_switched = dt.now()
            elif powerstep <= 500:
                if (
                    dt.now() - self.lasttime_switched
                ).total_seconds() >= MINIMUM_SWITCH_TIME / 10:
                    self.cmd_bits = self.calc_powercmd(
                        grid_surplus
                    )  # calculate power setting depending on energy surplus
                    self.lasttime_switched = dt.now()
            else:
                if (
                    dt.now() - self.lasttime_switched
                ).total_seconds() >= MINIMUM_SWITCH_TIME:
                    self.cmd_bits = self.calc_powercmd(
                        grid_surplus
                    )  # calculate power setting depending on energy surplus
                    self.lasttime_switched = dt.now()

            # but stop heating if target temperature is reached
            self.current_temperature = float(self.instrument.read_register(
                self.registers["Temperature"], 2, 4
            ))
            if self.current_temperature >= self.target_temperature:
                self.cmd_bits = [0, 0, 0]

            self.instrument.write_bits(self.registers["Power_500W"], self.cmd_bits)
            self.last_grid_surplus = grid_surplus

            self.current_power = int(self.instrument.read_register(
                self.registers["Power_Return"], 0, 4
            ))
            self.status = int(self.instrument.read_register(
                self.registers["Operation_Mode"], 0, 4
            ))
            self.exception_counter = 0  # reset counter after successful access

        except minimalmodbus.NoResponseError as e:  # TODO remove later
            self.exception_counter = 0
        except Exception as e:
            logging.info(e)
            if self.exception_counter >= self.Max_Retries:
                raise RuntimeError(f"Water Heater critical error, exiting {e}")
            self.exception_counter += 1


if __name__ == "__main__":
    # Setup logging
    logging.basicConfig(level=logging.INFO)

    # Parse command line arguments
    parser = argparse.ArgumentParser(description="Water Heater Modbus Interface")
    parser.add_argument(
        "--port",
        default="/dev/ttyUSB0",
        help="Modbus port (default: /dev/ttyUSB0)",
    )
    parser.add_argument(
        "--address",
        type=int,
        default=33,
        help="Modbus slave address (default: 33)",
    )
    args = parser.parse_args()

    # Initialize the instrument
    try:
        instrument = minimalmodbus.Instrument(args.port, args.address)
        instrument.serial.baudrate = 9600
    except Exception as e:
        logging.error(f"Failed to initialize instrument: {e}")
        sys.exit(1)

    # Initialize the WaterHeater class
    water_heater = WaterHeater(instrument)

    # Call the check_device_type function and print the result
    try:
        water_heater.check_device_type()
        print("Device type check passed.")
    except Exception as e:
        print(f"Device type check failed: {e}")
