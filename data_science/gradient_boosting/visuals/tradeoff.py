import matplotlib.pyplot as plt
import numpy as np

fig, ax = plt.subplots(figsize=(10, 9))

# Triangle vertices (equilateral)
# Top: Training Speed, Bottom-left: Robustness, Bottom-right: Inference Speed
vertices = np.array([
    [0.5, 0.93],   # Top - Training Speed
    [0.07, 0.15],  # Bottom-left - Robustness
    [0.93, 0.15]   # Bottom-right - Inference Speed
])

# Draw triangle
triangle = plt.Polygon(vertices, fill=False, edgecolor='#333333', linewidth=2)
ax.add_patch(triangle)

# Axis labels with icons
ax.text(0.5, 0.99, 'Training Speed', ha='center', va='bottom', fontsize=14, fontweight='bold')
ax.text(0.02, 0.08, 'Robustness', ha='left', va='top', fontsize=14, fontweight='bold')
ax.text(0.98, 0.08, 'Inference Speed', ha='right', va='top', fontsize=14, fontweight='bold')

# Algorithm positions (relative to triangle)
# LightGBM - high on training speed
lightgbm_pos = [0.5, 0.75]
# CatBoost - between robustness and inference (bottom, slightly left of center toward both corners)
catboost_pos = [0.5, 0.25]
# XGBoost - center (balanced)
xgboost_pos = [0.5, 0.50]

# Plot algorithms as colored circles
algorithms = {
    'LightGBM': {'pos': lightgbm_pos, 'color': '#4CAF50', 'desc': 'Fastest training\n(GOSS, EFB, leaf-wise)'},
    'XGBoost': {'pos': xgboost_pos, 'color': '#2196F3', 'desc': 'Balanced\n(well-understood)'},
    'CatBoost': {'pos': catboost_pos, 'color': '#FF9800', 'desc': 'Most robust +\nfastest inference'}
}

for name, data in algorithms.items():
    # Draw circle
    circle = plt.Circle(data['pos'], 0.08, color=data['color'], alpha=0.9, zorder=5)
    ax.add_patch(circle)

    # Algorithm name inside circle
    ax.text(data['pos'][0], data['pos'][1], name, ha='center', va='center',
            fontsize=11, fontweight='bold', color='white', zorder=6)

    # Add descriptions next to each
ax.text(0.75, 0.77, 'Fastest training\n(GOSS, EFB, leaf-wise)', ha='left', va='center',
        fontsize=10, style='italic', color='#4CAF50')
ax.text(0.75, 0.50, 'Balanced approach\n(well-understood)', ha='left', va='center',
        fontsize=10, style='italic', color='#2196F3')
ax.text(0.75, 0.25, 'Most robust defaults +\nfastest inference', ha='left', va='center',
        fontsize=10, style='italic', color='#FF9800')

# Draw connecting arrows from descriptions to circles
for y_pos, circle_y in [(0.77, 0.75), (0.50, 0.50), (0.25, 0.25)]:
    ax.annotate('', xy=(0.58, circle_y), xytext=(0.73, y_pos),
                arrowprops=dict(arrowstyle='->', color='gray', lw=1))

    # Title
ax.set_title('The Trade-Off Triangle\n"Pick your priority"', fontsize=18, fontweight='bold', pad=20)

# Clean up axes
ax.set_xlim(0, 1)
ax.set_ylim(0, 1.05)
ax.set_aspect('equal')
ax.axis('off')

plt.tight_layout()
plt.savefig('tradeoff_triangle.png', dpi=150, bbox_inches='tight', facecolor='white')
plt.show()

print("Saved as 'tradeoff_triangle.png'")
