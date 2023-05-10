# Third party
from torch import nn
import torch.nn.functional as F


class Interpolation(nn.Module):
    def __init__(self, size, mode):
        super().__init__()
        self.size = size
        self.mode = mode

    def forward(self, x):
        return F.interpolate(x, self.size, mode=self.mode)