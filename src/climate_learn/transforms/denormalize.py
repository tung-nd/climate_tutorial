# Standard library
from typing import Union

# Local application
from .registry import register
from ..data import DataModule, IterDataModule

# Third party
import torch
from torchvision import transforms


@register("denormalize")
class Denormalize:
    def __init__(self, data_module: Union[DataModule, IterDataModule]):
        super().__init__()
        norm = data_module.get_out_transforms()
        # Hotfix to work with dict style data
        mean_norm = torch.tensor([norm[k].mean for k in norm.keys()])
        std_norm = torch.tensor([norm[k].std for k in norm.keys()])
        std_denorm = 1 / std_norm
        mean_denorm = -mean_norm * std_denorm
        self.transform = transforms.Normalize(mean_denorm, std_denorm)

    def __call__(self, x) -> Union[torch.FloatTensor, torch.DoubleTensor]:
        return self.transform(x)