# 📚 GitHub Actions Documentation Pipeline

[![Documentation](https://img.shields.io/badge/docs-latest-blue)](https://yourusername.github.io/yourproject)
[![Build Status](https://github.com/yourusername/yourproject/workflows/Documentation%20Pipeline/badge.svg)](https://github.com/yourusername/yourproject/actions)
[![Coverage](https://img.shields.io/badge/docstring%20coverage-80%25-green)](https://yourusername.github.io/yourproject/coverage)

A comprehensive, production-ready GitHub Actions documentation pipeline for Python projects. This pipeline supports multiple documentation formats, extensive validation, and is particularly suited for mathematical and scientific documentation.

## 🚀 Features

### Core Capabilities
- **Multiple Documentation Formats**: Sphinx, MkDocs Material, and pdoc3
- **Multi-version Support**: Maintain documentation for multiple releases
- **Automated Deployment**: GitHub Pages, ReadTheDocs, and Netlify support
- **PR Previews**: Automatic preview deployments for pull requests
- **Comprehensive Validation**: Spell check, link validation, docstring coverage
- **Mathematical Content**: Full LaTeX/MathJax support for equations and formulas
- **Interactive Elements**: Live code playgrounds, API explorers, dark/light themes

### Quality Assurance
- ✅ Markdown and reStructuredText linting
- ✅ Spell checking with technical dictionary
- ✅ Broken link detection with retry logic
- ✅ Docstring coverage enforcement
- ✅ Code example validation
- ✅ Outdated content detection
- ✅ Documentation-code drift detection

### Advanced Features
- 🎨 Diagram generation (Mermaid, PlantUML, Graphviz)
- 📊 Architecture diagrams from code
- 🔍 Search functionality (Algolia, lunr.js)
- 🌍 Localization support
- 📱 Mobile-responsive documentation
- ♿ Accessibility validation (WCAG)
- 📈 Analytics integration
- 📝 Automatic changelog generation

## 📋 Prerequisites

### Required Files
Place these files in your repository:

```
.github/
├── workflows/
│   ├── documentation.yml          # Main pipeline
│   ├── docs-pr-preview.yml       # PR preview workflow
│   └── docs-link-checker.yml     # Scheduled link validation
├── actions/
│   └── build-docs/
│       └── action.yml             # Reusable composite action
└── markdown-link-check.json      # Link checker configuration

docs/
├── source/                        # Sphinx documentation
│   ├── conf.py                   # Sphinx configuration
│   ├── index.rst                 # Main index
│   └── _static/                  # Static assets
├── scripts/                       # Utility scripts
│   ├── check_docstring_coverage.py
│   ├── generate_api_docs.py
│   ├── validate_examples.py
│   └── generate_changelog.py
└── requirements.txt              # Documentation dependencies

mkdocs.yml                        # MkDocs configuration (optional)
README.md                         # Project readme
CONTRIBUTING.md                   # Contribution guidelines
CHANGELOG.md                      # Project changelog
```

### Repository Settings

1. **Enable GitHub Pages**:
   - Go to Settings → Pages
   - Source: Deploy from a branch
   - Branch: `gh-pages` / `/ (root)`

2. **Add Secrets** (optional):
   ```
   NETLIFY_AUTH_TOKEN     # For PR previews
   NETLIFY_SITE_ID        # For PR previews
   ```

3. **Branch Protection** (recommended):
   - Protect `main` branch
   - Require status checks
   - Include documentation checks

## 🔧 Installation

### Quick Start

1. **Copy the workflow files** to your repository:
   ```bash
   mkdir -p .github/workflows
   curl -O https://raw.githubusercontent.com/yourusername/docs-pipeline/main/.github/workflows/documentation.yml
   ```

2. **Install documentation dependencies**:
   ```bash
   pip install sphinx sphinx-rtd-theme mkdocs mkdocs-material pdoc3
   pip install sphinx-autodoc-typehints nbsphinx myst-parser
   ```

3. **Initialize documentation**:
   ```bash
   # For Sphinx
   sphinx-quickstart docs
   
   # For MkDocs
   mkdocs new .
   ```

4. **Customize the configuration** in the workflow file:
   - Update Python version if needed
   - Adjust minimum coverage threshold
   - Configure deployment settings

## 📖 Usage

### Automatic Triggers

The pipeline runs automatically on:
- **Push to main**: Deploys production documentation
- **Pull requests**: Builds and previews documentation
- **Tags/releases**: Creates versioned documentation
- **Weekly schedule**: Validates links and content freshness

### Manual Triggers

Run the pipeline manually from Actions tab with options:
- `rebuild_all`: Rebuild all documentation versions
- `skip_validation`: Skip validation checks
- `doc_format`: Choose specific format (sphinx/mkdocs/pdoc3/all)

### Writing Documentation

#### Sphinx (reStructuredText)
```rst
Mathematical Functions
======================

.. autofunction:: mypackage.math.calculate_derivative

The derivative is calculated using:

.. math::

   f'(x) = \lim_{h \to 0} \frac{f(x+h) - f(x)}{h}

Example usage::

    >>> from mypackage.math import calculate_derivative
    >>> calculate_derivative(lambda x: x**2, 3)
    6.0
```

#### MkDocs (Markdown)
```markdown
# Mathematical Functions

## Calculate Derivative

::: mypackage.math.calculate_derivative

The derivative is calculated using:

$$f'(x) = \lim_{h \to 0} \frac{f(x+h) - f(x)}{h}$$

!!! example "Usage"
    ```python
    from mypackage.math import calculate_derivative
    result = calculate_derivative(lambda x: x**2, 3)
    print(result)  # Output: 6.0
    ```
```

### Docstring Styles

The pipeline supports multiple docstring styles:

#### Google Style
```python
def function(param1: str, param2: int = 0) -> bool:
    """Brief description.
    
    Args:
        param1: First parameter description.
        param2: Second parameter description. Defaults to 0.
        
    Returns:
        Description of return value.
        
    Raises:
        ValueError: If param1 is empty.
        
    Examples:
        >>> function("test", 42)
        True
    """
```

#### NumPy Style
```python
def function(param1, param2=0):
    """
    Brief description.
    
    Parameters
    ----------
    param1 : str
        First parameter description.
    param2 : int, optional
        Second parameter description, by default 0.
        
    Returns
    -------
    bool
        Description of return value.
        
    Examples
    --------
    >>> function("test", 42)
    True
    """
```

## 🎯 Configuration

### Environment Variables

Configure the pipeline through environment variables in the workflow:

```yaml
env:
  PYTHON_VERSION: '3.11'          # Python version to use
  NODE_VERSION: '20'               # Node.js version for tools
  MIN_COVERAGE: 80                 # Minimum docstring coverage
  ENABLE_LOCALIZATION: true        # Enable multi-language support
  ENABLE_INTERACTIVE: true         # Enable interactive features
```

### Validation Rules

Customize validation in `.github/markdown-link-check.json`:

```json
{
  "ignorePatterns": [
    {"pattern": "^http://localhost"},
    {"pattern": "^https://github.com/.*/pull/"}
  ],
  "timeout": "20s",
  "retryOn429": true,
  "retryCount": 3
}
```

### Documentation Requirements

Set requirements in `docs/requirements.txt`:

```txt
# Core
sphinx>=7.0
mkdocs>=1.5
pdoc3>=0.10

# Themes
sphinx-rtd-theme>=2.0
mkdocs-material>=9.0

# Extensions
sphinx-autodoc-typehints>=1.25
nbsphinx>=0.9
myst-parser>=2.0
mkdocstrings[python]>=0.24

# Utilities
interrogate>=1.5
doc8>=1.1
rstcheck>=6.2
```

## 🔍 Validation Reports

The pipeline generates comprehensive validation reports:

### Docstring Coverage Report
```
============================================================
DOCSTRING COVERAGE REPORT
============================================================
✅ Modules: 100.0% (5/5)
✅ Classes: 95.2% (20/21)
✅ Functions: 88.9% (40/45)
✅ Methods: 92.3% (120/130)
------------------------------------------------------------
✅ TOTAL: 91.3% (185/201)
```

### Link Validation Report
```
📅 Weekly Documentation Health Check

## Broken Links
- [ ] https://oldapi.example.com in `docs/api.md`
- [ ] https://broken.link.com in `README.md`

## Outdated Content (>6 months)
- `docs/tutorials/old-tutorial.md` - Last modified: 2023-06-15 (425 days ago)
```

## 🚀 Advanced Features

### Multi-Version Documentation

The pipeline automatically manages multiple documentation versions:

1. **Latest**: From main branch
2. **Stable**: Latest tagged release
3. **Development**: From develop branch
4. **Version Tags**: Each release tag (v1.0.0, v2.0.0, etc.)

Access versions at:
- `https://yourusername.github.io/project/latest/`
- `https://yourusername.github.io/project/v1.0.0/`
- `https://yourusername.github.io/project/stable/`

### PR Preview Deployments

Pull requests automatically get preview deployments:

```markdown
📚 Documentation Preview

The documentation has been built and is available for preview:

🔗 **Preview URL**: https://project-pr-123.netlify.app

### Build Summary:
- **Version**: pr-123
- **Formats Built**: sphinx, mkdocs, pdoc3
- **Status**: ✅ Success
```

### Mathematical Documentation

Full support for mathematical content:

- LaTeX equations in Markdown and RST
- Jupyter notebook integration
- Mathematical proof formatting
- Algorithm pseudocode blocks
- Citation management (BibTeX)

### Interactive Features

Enable interactive documentation elements:

- Live Python code execution (Pyodide)
- API request builders
- Interactive diagrams
- Embedded Jupyter notebooks
- Code playground widgets

## 📊 Monitoring

### GitHub Actions Summary

The pipeline provides detailed summaries after each run:

```markdown
# 📚 Documentation Pipeline Summary

## 📊 Build Information
- **Version**: 1.2.3
- **Is Release**: true
- **Documentation Format**: sphinx
- **Should Deploy**: true

## ✅ Pipeline Status
| Job | Status |
|-----|--------|
| Setup | success |
| Validation | success |
| Build | success |
| Deploy | success |
```

### Metrics and Analytics

Track documentation quality metrics:
- Build time trends
- Coverage over time
- Link rot detection
- User engagement (with analytics)
- Search query analysis

## 🛠️ Troubleshooting

### Common Issues

#### Build Failures
```bash
# Check for syntax errors in conf.py
python docs/source/conf.py

# Validate RST files
rstcheck docs/**/*.rst

# Test Sphinx build locally
sphinx-build -b html docs/source docs/_build
```

#### Low Coverage
```bash
# Run coverage check locally
python docs/scripts/check_docstring_coverage.py coverage --path src/

# Generate coverage badge
interrogate src --generate-badge docs/coverage.svg
```

#### Broken Links
```bash
# Check links locally
npm install -g markdown-link-check
find . -name "*.md" -exec markdown-link-check {} \;
```

### Performance Optimization

Speed up builds with caching:
- Dependencies are cached by default
- Documentation builds are incremental
- Use matrix strategies for parallel builds
- Enable artifact compression

## 📚 Best Practices

1. **Keep Documentation Close to Code**
   - Write docstrings immediately after implementation
   - Update docs in the same PR as code changes
   - Use type hints for better autodoc

2. **Use Semantic Versioning**
   - Tag releases properly (v1.0.0, v2.0.0)
   - Maintain a CHANGELOG.md
   - Document breaking changes

3. **Optimize for Readers**
   - Start with quickstart guides
   - Provide complete examples
   - Include troubleshooting sections
   - Add search functionality

4. **Maintain Quality**
   - Set minimum coverage thresholds
   - Fix broken links immediately
   - Review outdated content regularly
   - Test code examples

## 🤝 Contributing

Contributions are welcome! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

### Development Setup

```bash
# Clone the repository
git clone https://github.com/yourusername/yourproject.git
cd yourproject

# Install development dependencies
pip install -e ".[dev,docs]"

# Run documentation locally
mkdocs serve  # For MkDocs
# or
sphinx-autobuild docs/source docs/_build  # For Sphinx

# Run tests
python -m pytest tests/
python docs/scripts/validate_examples.py validate --path docs/
```

## 📄 License

This documentation pipeline is available under the MIT License. See [LICENSE](LICENSE) for details.

## 🙏 Acknowledgments

This pipeline incorporates best practices from:
- [Sphinx Documentation](https://www.sphinx-doc.org/)
- [MkDocs Material](https://squidfunk.github.io/mkdocs-material/)
- [GitHub Actions Documentation](https://docs.github.com/en/actions)
- [Write the Docs](https://www.writethedocs.org/)

## 📮 Support

- 📧 Email: support@example.com
- 💬 Discussions: [GitHub Discussions](https://github.com/yourusername/yourproject/discussions)
- 🐛 Issues: [GitHub Issues](https://github.com/yourusername/yourproject/issues)
- 📖 Documentation: [Latest Docs](https://yourusername.github.io/yourproject)

---

*Generated with the GitHub Actions Documentation Pipeline v1.0*
