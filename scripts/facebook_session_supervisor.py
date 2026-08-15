"""Supervise the operator desktop; fail/restart if any component exits."""
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import time


def connectable(address, family):
