import matplotlib.pyplot as plt
import numpy as np

# Generate 10k salaries with realistic right-skewed distribution
np.random.seed(42)
salaries = np.random.exponential(scale=45000, size=10000)
salaries = np.clip(salaries, 1, 600000)  # Clip to 1€ - 600,000€

# Create histogram with 15 bins
fig, ax = plt.subplots(figsize=(12, 6))

counts, bins, patches = ax.hist(salaries, bins=15, edgecolor='black', color='#4C9AFF')

# Add count labels on top of each bar
for count, patch in zip(counts, patches):
    ax.text(patch.get_x() + patch.get_width()/2, patch.get_height() + 100,
            f'{int(count)}', ha='center', va='bottom', fontsize=10, fontweight='bold')

    # Formatting
ax.set_xlabel('Salary (€)', fontsize=14)
ax.set_ylabel('Number of Customers', fontsize=14)
ax.set_title('600,000 possible values → 15 bins\n10,000 customers', fontsize=16, fontweight='bold')

# Format x-axis with € and thousands separator
ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'{int(x):,}€'))

# Add annotation
ax.annotate('Instead of evaluating 10,000 split points\n→ only 15 bin boundaries to check!',
            xy=(300000, max(counts)*0.7), fontsize=12,
            bbox=dict(boxstyle='round', facecolor='yellow', alpha=0.7))

plt.tight_layout()
plt.savefig('histogram_binning.png', dpi=150, bbox_inches='tight')
plt.show()

print(f"\nBin boundaries:")
for i, b in enumerate(bins):
    print(f"  Bin {i}: {b:,.0f}€")
