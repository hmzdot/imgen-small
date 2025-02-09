import torch
import torch.optim as optim
import logging
import itertools
from tqdm import tqdm
from datetime import datetime
from torchvision import datasets, transforms
from torch.utils.data import DataLoader
from src.model import Generator, Critic
from torch.utils.tensorboard import SummaryWriter
from typing import Union, Literal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)


celeba_dataset = datasets.CelebA(
    root="./data",
    split="train",
    download=True,
    transform=transforms.Compose(
        [
            transforms.CenterCrop(178),
            transforms.Resize(128),
            transforms.RandomHorizontalFlip(),
            transforms.ToTensor(),
            transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5)),
        ]
    ),
)
loader = DataLoader(celeba_dataset, batch_size=64, shuffle=True)


def get_device() -> torch.device:
    if torch.cuda.is_available():
        return torch.device("cuda")
    elif torch.backends.mps.is_available():
        return torch.device("mps")
    else:
        return torch.device("cpu")


def gradient_penalty(D, real_samples, fake_samples, device, factor):
    real_samples = real_samples.float()
    fake_samples = fake_samples.float()

    # Get the batch size and shape information
    batch_size = real_samples.size(0)
    # Create alpha with the correct shape for broadcasting

    # Linearly interpolate distribution with:
    # x_interpolated = alpha * x_real + (1-alpha) * x_gen
    alpha = torch.rand((batch_size, 1, 1, 1), device=device)
    interpolates = alpha * real_samples + ((1 - alpha) * fake_samples)
    interpolates.requires_grad_(True)
    d_interpolates = D(interpolates)

    # Take gradient of D's output wrt. interpolates
    gradients = torch.autograd.grad(
        outputs=d_interpolates,
        inputs=interpolates,
        grad_outputs=torch.ones_like(d_interpolates),
        create_graph=True,
        retain_graph=True,
        only_inputs=True,
    )[0]
    gradients = gradients.view(gradients.size(0), -1)

    # Calculate E[ (L2Norm(grad) - 1)^2 ]
    # L2 Norm can be calculated with Tensor::norm
    # Expected value (E) can be calculated with Tensor::mean
    gradient_norms = gradients.norm(2, dim=1).clamp(min=1e-6)
    gradient_penalty = ((gradient_norms - 1) ** 2).mean()
    return factor * gradient_penalty


def critic_loss(D, real_samples, fake_samples, device, factor=10):
    # For critic we want to maximize D(real) - D(fake)
    # When using an optimizer that minimizes, we define:
    loss = torch.mean(D(fake_samples)) - torch.mean(D(real_samples))
    gp = gradient_penalty(D, real_samples, fake_samples, device, factor)
    return loss + gp


def generator_loss(D, fake_samples):
    # For generator we want to maximize D(fake), which is equivalent to minimizing -D(fake)
    loss = -torch.mean(D(fake_samples))
    return loss


def train(
    G,
    D,
    loader,
    epochs: Union[int, Literal["inf"]] = 100,
    n_critic=5,
    lr=0.00005,
    betas=(0.5, 0.999),
    factor=10,
):
    """
    Trains a GAN model.
    # Parameters
    - G: Generator model
    - D: Discriminator model
    - loader: DataLoader for the dataset
    - epochs: Number of epochs to train for, or "inf" to run indefinitely
    - n_critic: Number of critic updates per generator update
    - lr: Learning rate
    """
    # Initialize TensorBoard writer
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    writer = SummaryWriter(f"runs/wgan_wc_{timestamp}")

    device = get_device()
    G.to(device)
    D.to(device)

    optimizer_G = optim.Adam(G.parameters(), lr=lr, betas=betas)
    optimizer_D = optim.Adam(D.parameters(), lr=lr, betas=betas)

    if epochs == "inf":
        epochs_range = itertools.count()
    else:
        epochs_range = range(epochs)

    writer_step = 0
    for epoch in epochs_range:
        for real_img, _ in tqdm(loader, desc=f"Epoch #{epoch + 1}"):
            # Move real_img to the same device
            real_img = real_img.to(device)
            batch_size = real_img.size(0)

            # Train discriminator / critic
            # For every G step, we train the D n_critic times
            for _ in range(n_critic):
                optimizer_D.zero_grad()
                z = torch.randn(batch_size, 100, 1, 1, device=device)
                gen_img = G(z).detach()

                loss_D = critic_loss(
                    D,
                    real_img,
                    gen_img,
                    device=device,
                    factor=factor,
                )
                loss_D.backward()

                optimizer_D.step()

            # Train generator
            optimizer_G.zero_grad()
            z = torch.randn(batch_size, 100, 1, 1, device=device)
            fake_img = G(z)
            loss_G = generator_loss(D, fake_img)
            loss_G.backward()
            optimizer_G.step()

            # Log losses to TensorBoard
            writer.add_scalar("Loss/Critic", loss_D.item(), writer_step)
            writer.add_scalar("Loss/Generator", loss_G.item(), writer_step)
            writer_step += 1

        # Log sample images
        with torch.no_grad():
            sample_z = torch.randn(8, 100, 1, 1, device=device)
            sample_images = G(sample_z)
            # Rescale images from [-1, 1] to [0, 1]
            sample_images = (sample_images + 1) / 2
            writer.add_images("Generated Images", sample_images, epoch)

        # Save models
        torch.save(G.state_dict(), f"./snapshots/gw_{timestamp}.pth")
        torch.save(D.state_dict(), f"./snapshots/dw_{timestamp}.pth")

        logging.info(
            f"Saved models to snapshots/gw_{timestamp}.pth and snapshots/dw_{timestamp}.pth"
        )

        logging.info(
            f"Epoch #{epoch + 1}, D loss: {loss_D.item():.4f}, G loss: {loss_G.item():.4f}"
        )

    # Close TensorBoard writer
    writer.close()


if __name__ == "__main__":
    train(
        Generator(img_channels=3, img_size=128),
        Critic(img_channels=3, img_size=128),
        loader,
    )
