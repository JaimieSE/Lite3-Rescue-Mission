from setuptools import setup

setup(
    name='rescue_mission', version='0.1.0',
    packages=['rescue_mission'],
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/rescue_mission']),
        ('share/rescue_mission', ['package.xml']),
        ('share/rescue_mission/config', ['config/objectives.json']),
    ],
    install_requires=['setuptools'], zip_safe=True,
    maintainer='Richard', maintainer_email='richard@example.invalid',
    description='Persistent mission objectives and evidence, independent of locomotion.',
    license='Apache-2.0',
    entry_points={'console_scripts': [
        'mission_store = rescue_mission.cli:main',
        'mission_node = rescue_mission.node:main',
        'front_camera = rescue_mission.front_camera:main',
    ]},
)
