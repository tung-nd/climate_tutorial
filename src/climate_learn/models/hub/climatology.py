# Third party
from torch import nn


class Climatology(nn.Module):
    def __init__(self, clim):
        super().__init__()
        self.clim = clim  # clim.shape = [C,H,W]

    def forward(self, x):
        # x.shape = [B,T,C,H,W]
        yhat = self.clim.unsqueeze(0).repeat(x.shape[0], 1, 1, 1)
        # yhat.shape = [B,C,H,W]
        return yhat