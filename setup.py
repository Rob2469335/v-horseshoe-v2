from setuptools import setup, find_packages

setup(
    name='dead-code-cleaner',
    version='0.1.0',
    packages=find_packages(),
    install_requires=[
        'astroid>=1.6.0',
        'vulture>=2.3',
        'pytest>=5.0.0',
    ],
    entry_points={
        'console_scripts': [
            'dead-code-cleaner=dead_code_cleaner:main',
        ],
    },
    author='Your Name',
    author_email='your.email@example.com',
    description='A tool to detect and remove dead code from Python projects',
    long_description=open('README.md').read(),
    long_description_content_type='text/markdown',
    url='https://github.com/yourusername/dead-code-cleaner',
    classifiers=[
        'Programming Language :: Python :: 3',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
    ],
    python_requires='>=3.6',
)