import io
import os

from setuptools import find_packages, setup


here = os.path.abspath(os.path.dirname(__file__))
with io.open(os.path.join(here, 'README.md'), encoding='utf-8') as f:
    long_description = '\n' + f.read()

setup(
    name='ec2instance',
    version='1.3',
    description="Quickly launch an EC2 instance for small tasks",
    keywords='ec2instance',
    long_description=long_description,
    long_description_content_type='text/markdown',
    author="John Miller",
    author_email='john@johngm.com',
    python_requires='>=3.7.0',
    url='https://github.com/personalcomputer/ec2instance',
    entry_points={
        'console_scripts': ['ec2instance=ec2instance.main:main'],
    },
    packages=find_packages(include=['ec2instance', 'ec2instance.*']),
    install_requires=[
        'boto3',
        'iso8601',
        'pycryptodomex',
    ],
    include_package_data=True,
    license='MIT',
    classifiers=[
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3',
    ],
    zip_safe=False
)
