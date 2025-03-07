import numpy as np
import matplotlib.pyplot as plt

# Define performance categories (metrics)
categories = ["Execution Speed", "Energy", "RAM", "ROM", "E-RANK"]
num_vars = len(categories)

# Lightweight hash functions being compared
hash_functions = ["ASCON", "PHOTON-BEETLE", "XOODYAK", "SPARKLE", "ISAP"]

# Raw benchmarking values
cpb = [2531, 2158, 2450, 1633, 3953]  # Cycles per Byte (Lower is better)
energy = [0.99, 0.96, 0.90, 0.92, 0.99]  # Energy in nJ (Lower is better)
ram = [153, 263, 390, 396, 250]  # RAM in bytes (Lower is better)
rom = [2648, 1936, 2804, 3990, 4040]  # ROM in bytes (Lower is better)
e_rank = [135.1, 196.06, 126.54, 139.19, 56.28]  # E-RANK (Higher is better)

# Normalize data (scaling values between 0 and 1 for visualization)
cpb_norm = [max(cpb) / val for val in cpb]  # Lower CpB is better, so we invert it
energy_norm = [min(energy) / val for val in energy]  # Lower energy is better
ram_norm = [min(ram) / val for val in ram]  # Lower RAM is better
rom_norm = [min(rom) / val for val in rom]  # Lower ROM is better
e_rank_norm = [val / max(e_rank) for val in e_rank]  # Higher E-RANK is better

# Combine normalized data
data = np.array([cpb_norm, energy_norm, ram_norm, rom_norm, e_rank_norm]).T

# Generate angles for radar chart
angles = np.linspace(0, 2 * np.pi, num_vars, endpoint=False).tolist()
angles += angles[:1]  # Complete the loop

# Initialize figure
fig, ax = plt.subplots(figsize=(8, 8), subplot_kw=dict(polar=True))

# Define colors for better visualization
colors = ['blue', 'green', 'red', 'purple', 'orange']
fill_colors = ['skyblue', 'lightgreen', 'salmon', 'violet', 'gold']

# Plot each hash function
for i, label in enumerate(hash_functions):
    values = data[i].tolist()
    values += values[:1]  # Complete the loop
    ax.plot(angles, values, color=colors[i], linewidth=2, label=label)
    ax.fill(angles, values, color=fill_colors[i], alpha=0.15)  # Fill the area under the curve

# Set axis labels and title
ax.set_xticks(angles[:-1])
categories[0] = "Exec\nSpeed"  # Modify the first category to be on two lines
ax.set_xticklabels(categories, fontsize=20, color='black', fontweight='bold')
# Check if the label is "Exec\nSpeed" and left indent it
for label in ax.get_xticklabels():
    if label.get_text() == "Exec\nSpeed":
        label.set_horizontalalignment('left')

# Move the labels farther away from the circle
for label, angle in zip(ax.get_xticklabels(), angles):
    x, y = label.get_position()
    label.set_position((x, y - 0.05))
    if label.get_text() == "Exec\nSpeed":
        label.set_position((x, y + 0.04))

# Move the reference values (r-ticks) closer to the "Energy" axis
energy_angle_index = 1  # "Energy" is at index 1 in the categories list
energy_angle = angles[energy_angle_index]  # Get its angle

# Shift r-ticks closer to "Energy"
ax.yaxis.set_tick_params(pad=1)  # Adjust padding to move reference values outward

# Align labels towards "Energy" and make them bold
for label in ax.get_yticklabels():
    if label.get_text() == '2.5':  # Skip the label with text '2.5'
        label.set_visible(False)
        continue
    x, y = label.get_position()
    label.set_position((x + 2 * np.cos(energy_angle), y + 9 * np.sin(energy_angle)))
    label.set_horizontalalignment("center")  # Keep labels readable
    label.set_fontweight('bold')  # Make labels bold
    label.set_fontsize(16)

# Move the legend to the upper left inside the plot
plt.legend(loc="upper left", bbox_to_anchor=(-0.2, 1.1), fontsize=12, frameon=True)

# Improve readability with grid lines
ax.grid(True, linestyle='--', linewidth=1.2, alpha=0.7)

# Display the chart
plt.savefig("radar_chart.pdf", format="pdf", bbox_inches="tight")
plt.show()