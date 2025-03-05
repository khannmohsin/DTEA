import numpy as np
import matplotlib.pyplot as plt
from scipy.spatial import distance

# Function to generate a 2D lattice
def generate_lattice(basis, x_range, y_range):
    points = []
    for x1 in range(-x_range, x_range):
        for x2 in range(-y_range, y_range):
            point = x1 * basis[:, 0] + x2 * basis[:, 1]
            points.append(point)
    return np.array(points)

# Define basis vectors for the lattice
basis = np.array([[2, 9],
                  [7, 13]])

# Generate lattice points
lattice_points = generate_lattice(basis, 4, 4)

# =====================
# CVP EXAMPLE
# =====================
# Let's define one or more target points for CVP
cvp_targets = [np.array([7, 10]), np.array([10, 20])]  # Example target points

# For each target, find the lattice point closest to it
cvp_solutions = []
for target in cvp_targets:
    # Find the closest lattice point using Euclidean distance
    closest_point = min(lattice_points, key=lambda p: distance.euclidean(p, target))
    cvp_solutions.append((target, closest_point))

# Plot Lattice with CVP example
fig, ax = plt.subplots(figsize=(8, 8))

# Plot all lattice points
ax.scatter(lattice_points[:, 0], lattice_points[:, 1],
           color='gray', label='Lattice Points', s=50)

# Plot each CVP target and draw an arrow to the nearest lattice point
for target, closest in cvp_solutions:
    # Plot the target point
    ax.scatter(target[0], target[1], color='blue', marker='x', s=100,
               label='CVP Target')
    # Draw an arrow from closest lattice point to the target
    ax.quiver(closest[0], closest[1],
              target[0] - closest[0], target[1] - closest[1],
              angles='xy', scale_units='xy', scale=1,
              color='green', linewidth=1, label='CVP Solution')

    # Optionally label the closest point
    ax.text(closest[0], closest[1]+3,
            f'Closest: ({closest[0]},{closest[1]})',
            fontsize=8, ha='center', color='green')

ax.set_title("Visualization of CVP in a Lattice")
ax.axhline(0, color='black', linewidth=0.5)
ax.axvline(0, color='black', linewidth=0.5)
ax.legend(loc="upper left")
ax.grid(True)

# Show the plot
plt.show()