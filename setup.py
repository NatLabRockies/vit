from setuptools import setup, find_packages

setup(
    name="vit",
    version="0.1.0",
    description="Verified Incremental Translator — trustworthy Fortran→C++ translation pipeline",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        "numpy",
        "pyyaml>=6.0",
    ],
    extras_require={
        # `vit inventory` parses Fortran with fparser2.
        "inventory": ["fparser"],
    },
    entry_points={
        "console_scripts": [
            "vit=vit.cli:main",
        ],
    },
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Science/Research",
        "Topic :: Scientific/Engineering",
        "Programming Language :: Python :: 3",
        "Programming Language :: Fortran",
    ],
)
