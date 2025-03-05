from Node_activator.activatorNode import Activator

class Edge:
    def __init__(self, edge_id, location):
        self.edge_id = edge_id
        self.location = location
        self.sensor_data = {}  # Store data received from sensors

    def receive_data(self, sensor_id, data):
        # Store the received data
        self.sensor_data[sensor_id] = data
        print(f"Edge Node {self.edge_id} received data from Sensor {sensor_id}: {data}")

    def process_data(self, sensor_id, data, activator):
        # Process the data and take action if needed
        if data > 25:  # Example threshold for temperature
            print(f"Edge Node {self.edge_id} processing data: {data} from Sensor {sensor_id}")
            activator.activate(data)
        else:
            activator.deactivate()

    def display_data(self):
        # Display all stored sensor data
        print(f"Edge Node {self.edge_id} stored data: {self.sensor_data}")