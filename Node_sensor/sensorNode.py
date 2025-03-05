# sensor.py

import random

class Sensor:
    def __init__(self, sensor_id, sensor_type, location):
        self.sensor_id = sensor_id
        self.sensor_type = sensor_type
        self.location = location

    def generate_data(self):
        if self.sensor_type == "temperature":
            temperature = round(random.uniform(20, 30), 2)  # generate random temperature
            print(f"Temperature: {temperature}")
            return temperature
        elif self.sensor_type == "humidity":
            humidity = round(random.uniform(40, 60), 2)
            print(f"Humidity: {humidity}")
            return humidity
        elif self.sensor_type == "light":
            light = random.randint(0, 1000)
            print(f"Light: {light}")
            return light
        return None
    
    def display_data(self):
        data = self.generate_data()
        print(f"Sensor {self.sensor_id} ({self.sensor_type}) location: {self.location}")

    def send_data(self, edge_node):
        data = self.generate_data()
        print(f"Sensor {self.sensor_id} ({self.sensor_type}) sends data: {data}")
        edge_node.receive_data(self.sensor_id, data)\

    def send_data_to_activator(self, activator):
        # Generate data and send it to the activator
        if self.sensor_type == "temperature":  # Only send temperature data for this example
            temperature = self.generate_data()
            print(f"Sensor {self.sensor_id} ({self.sensor_type}) in {self.location} sends data: {temperature}")
            activator.process_sensor_data(temperature)

# sensor = Sensor(1, "temperature", "room1")
# sensor2 = Sensor(2, "humidity", "room2")
# sensor3 = Sensor(3, "light", "room3")
# sensor.generate_data()
# sensor2.generate_data()
# sensor3.generate_data()

# sensor.display_data()

