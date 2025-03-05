import numpy as np
import matplotlib.pyplot as plt

# Function to generate a 2D lattice
def generate_lattice(basis, x_range, y_range):
    points = []
    for x1 in range(-x_range, x_range):
        for x2 in range(-y_range, y_range):
            point = x1 * basis[:, 0] + x2 * basis[:, 1]
            points.append(point)
    return np.array(points)

# Define basis vectors for the lattice
basis = np.array([[2, 9], [7, 13]])

# Generate lattice points
lattice_points = generate_lattice(basis, 10, 10)

# Define SVP example
svp_vectors = [point for point in lattice_points if not np.array_equal(point, [0, 0])]
shortest_vector = min(svp_vectors, key=lambda v: np.linalg.norm(v))

# Plot Lattice with SVP example
fig, ax = plt.subplots(figsize=(8, 8))
ax.scatter(lattice_points[:, 0], lattice_points[:, 1], color='gray', label='Lattice Points', s=50)
ax.quiver(0, 0, shortest_vector[0], shortest_vector[1], angles='xy', scale_units='xy', scale=1, color='red', label='Shortest Vector (SVP)', linewidth=1)

# # Annotate only the shortest vector
ax.text(
    shortest_vector[0],
    shortest_vector[1] + 3,  # Add a small vertical offset (e.g., +0.2)
    f'({shortest_vector[0]},{shortest_vector[1]})',
    fontsize=12,
    ha='right',
    color='red',
    fontweight='bold'
)

ax.set_title("Visualization of SVP in a Lattice")
ax.axhline(0, color='black', linewidth=0.5)
ax.axvline(0, color='black', linewidth=0.5)
ax.legend(loc="upper left")
ax.grid(True)

# Show the plot
plt.show()