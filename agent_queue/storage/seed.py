"""Seed data for initial database population."""

import logging

from .database import db
from .models import TaskCreate

logger = logging.getLogger(__name__)

SEED_TASKS = [
    TaskCreate(
        title="Train MNIST classifier end-to-end",
        description="""Build a complete MNIST handwritten digit classification pipeline from scratch.

Requirements:
1. Create a new Python project directory at ~/mnist-classifier/
2. Set up a virtual environment with PyTorch and torchvision
3. Implement the training pipeline:
   - Download MNIST dataset using torchvision.datasets
   - Define a CNN model (Conv2d -> ReLU -> MaxPool -> FC layers)
   - Training loop with Adam optimizer and CrossEntropyLoss
   - Track training loss and accuracy per epoch
   - Train for 5 epochs
4. Evaluate on the test set and print final accuracy
5. Save the trained model to mnist_model.pt
6. Create a simple inference script that loads the model and classifies a sample digit
7. Add a README.md with instructions to run

The final test accuracy should be >98%. Log all training metrics to stdout.""",
        priority=1,
        metadata={
            "tags": ["ml", "pytorch", "starter"],
            "working_directory": "~/mnist-classifier",
        },
    ),
]


async def seed_database():
    """Insert seed tasks if the database is empty."""
    for task_data in SEED_TASKS:
        exists = await db.task_exists(task_data.title)
        if not exists:
            task = await db.create_task(task_data)
            logger.info(f"Seeded task: {task.title} (id={task.id})")
        else:
            logger.debug(f"Seed task already exists: {task_data.title}")
