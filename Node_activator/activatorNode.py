import random
from sensorNode import Sensor

class Activator:
    def __init__(self, activator_id, action_type, location):
        # Initialize attributes
        self.activator_id = activator_id
        self.action_type = action_type  # Action the activator performs (e.g., "cooling", "lighting")
        self.location = location  # Location of the activator
        self.state = "OFF"  # Initial state of the activator

    def activate(self, action_value):

        # Perform activation
        self.state = "ON"
        print(f"Activator {self.activator_id} in {self.location} performing '{self.action_type}' with value: {action_value}")

    def deactivate(self):
        # Deactivate the activator
        self.state = "OFF"
        print(f"Activator {self.activator_id} in {self.location} has been deactivated.")

    def display_status(self):
        # Display the current status of the activator
        print(f"Activator {self.activator_id} - Location: {self.location}, Action: {self.action_type}, State: {self.state}")


    def process_sensor_data(self, temperature):
        # Decide whether to activate or deactivate based on temperature
        if temperature > 25:  # Example threshold
            self.activate(temperature)
            print(self.display_status())
        else:
            self.deactivate()
            print(self.display_status())

# # Create an instance of the Activator class
# activator = Activator(1, "cooling", "room1")

# # Activate the activator
# activator.activate(25)

# # Display the status of the activator
# activator.display_status()