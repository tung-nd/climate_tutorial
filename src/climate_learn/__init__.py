# Standard library
from typing import Any, Callable, Dict, Iterable, Optional, Union
import warnings

# Local application
from .data import IterDataModule, DataModule
from .metrics import (
    Denormalized,
    MetricsMetaInfo,
    LatWeightedMSE,
    LatWeightedRMSE,    
    LatWeightedACC,
    MSE,
    RMSE,
    Pearson,
    MeanBias
)
from .models import LitModule
from .models.hub import MODEL_REGISTRY
from .models.lr_scheduler import LinearWarmupCosineAnnealingLR

# Third party
import torch
import torch.nn as nn
from torchvision import transforms


def load_forecasting_module(
    data_module: Union[DataModule, IterDataModule],
    preset: Optional[str] = None,
    model: Optional[str] = None,
    model_kwargs: Optional[Dict[str, Any]] = None,
    optim: Optional[str] = None,
    optim_kwargs: Optional[Dict[str, Any]] = None,
    lr_kwargs: Optional[Dict[str, Any]] = None,
    net: Optional[nn.Module] = None,
    optimizer: Optional[Dict[str, torch.optim.Optimizer]] = None,
    train_loss: Optional[Iterable[Callable]] = None,
    val_loss: Optional[Iterable[Callable]] = None,
    test_loss: Optional[Iterable[Callable]] = None
):
    in_vars = data_module.hparams.train_dataset_args.task_args.in_vars
    out_vars = data_module.hparams.train_dataset_args.task_args.out_vars
    history = data_module.hparams.train_dataset_args.task_args.history
    model = _validate_model_kwargs(preset, model, model_kwargs, net)
    if model:
        model_cls = MODEL_REGISTRY.get(model, None)
        if model_cls is None:
            raise NotImplementedError(
                f"{model} is not an implemented model. If you think it should be,"
                " please raise an issue at"
                " https://github.com/aditya-grover/climate-learn/issues."
            )
        if model == "climatology":
            train_climatology = data_module.get_climatology(split="train")
            train_climatology = torch.stack(tuple(train_climatology.values()))
            net = model_cls(train_climatology)
        elif model == "persistence":
            if not set(out_vars).issubset(in_vars):
                raise RuntimeError(
                    "Persistence requires the output variables to be a subset of"
                    " the input variables."
                )
            net = model_cls()
        elif model == "linear-regression":
            train_climatology = data_module.get_climatology(split="train")
            train_climatology = torch.stack(tuple(train_climatology.values()))
            in_features = train_climatology.flatten().shape * history
            test_climatology = data_module.get_climatology(split="test")
            test_climatology = torch.stack(tuple(test_climatology.values()))
            out_features = test_climatology.flatten().shape
            net = model_cls(in_features, out_features)
        elif model == "rasp-theurey-2020":
            net = model_cls(
                in_channels=len(in_vars),
                history=3,
                hidden_channels=128,
                activation="leaky",
                out_channels=len(out_vars),
                norm=True,
                dropout=0.1,
                n_blocks=19
            )
    optimizer = _load_optimizer(net, optim, optim_kwargs, lr_kwargs, optimizer)
    if train_loss is None:
        train_metainfo = MetricsMetaInfo(
            in_vars,
            out_vars,
            *data_module.get_lat_lon(), 
            _get_climatology(data_module, "train")
        )
        train_loss = nn.ModuleList([LatWeightedMSE(metainfo=train_metainfo)])
    if val_loss is None:
        val_metainfo = MetricsMetaInfo(
            in_vars,
            out_vars,
            *data_module.get_lat_lon(),
            _get_climatology(data_module, "val")
        )
        val_loss = nn.ModuleList([
            LatWeightedMSE(metainfo=val_metainfo),
            LatWeightedRMSE(metainfo=val_metainfo),
            Denormalized(
                _get_denorm(data_module),
                LatWeightedACC(metainfo=val_metainfo)
            )
        ])
    if test_loss is None:
        test_metainfo = MetricsMetaInfo(
            in_vars,
            out_vars,
            *data_module.get_lat_lon(),
            _get_climatology(data_module, "test")
        )
        test_loss = nn.ModuleList([
            LatWeightedRMSE(metainfo=test_metainfo),
            Denormalized(
                _get_denorm(data_module),
                LatWeightedACC(metainfo=test_metainfo)
            )
        ])
    model_module = LitModule(net, optimizer, train_loss, val_loss, test_loss)
    return model_module

def load_downscaling_module(
    data_module: Union[DataModule, IterDataModule],
    preset: Optional[str] = None,
    model: Optional[str] = None,
    model_kwargs: Optional[Dict[str, Any]] = None,
    optim: Optional[str] = None,
    optim_kwargs: Optional[Dict[str, Any]] = None,
    lr_kwargs: Optional[Dict[str, Any]] = None,
    net: Optional[nn.Module] = None,
    optimizer: Optional[Dict[str, torch.optim.Optimizer]] = None,
    train_loss: Optional[Iterable[Callable]] = None,
    val_loss: Optional[Iterable[Callable]] = None,
    test_loss: Optional[Iterable[Callable]] = None
):
    in_vars = data_module.hparams.train_dataset_args.task_args.in_vars
    out_vars = data_module.hparams.train_dataset_args.task_args.out_vars
    model = _validate_model_kwargs(preset, model, model_kwargs, net)
    if model:
        model_cls = MODEL_REGISTRY.get(model, None)
        if model_cls is None:
            raise NotImplementedError(
                f"{model} is not an implemented model. If you think it should be,"
                " please raise an issue at"
                " https://github.com/aditya-grover/climate-learn/issues."
            )
        elif model.endswith("interpolation"):
            interpolation_mode = model.split("_")[0]            
            train_climatology = data_module.get_climatology(split="train")
            train_climatology = torch.stack(tuple(train_climatology.values()))
            size = train_climatology.shape
            net = model_cls(size, interpolation_mode)
    optimizer = _load_optimizer(net, optim, optim_kwargs, lr_kwargs, optimizer)
    if train_loss is None:
        train_metainfo = MetricsMetaInfo(
            in_vars,
            out_vars,
            *data_module.get_lat_lon(), 
            _get_climatology(data_module, "train")
        )        
        train_loss = nn.ModuleList([MSE(metainfo=train_metainfo)])
    if val_loss is None:
        val_metainfo = MetricsMetaInfo(
            in_vars,
            out_vars,
            *data_module.get_lat_lon(),
            _get_climatology(data_module, "val")
        )
        val_loss = nn.ModuleList([
            RMSE(metainfo=val_metainfo),
            Denormalized(
                _get_denorm(data_module),
                Pearson(metainfo=val_metainfo)
            ),
            Denormalized(
                _get_denorm(data_module),
                MeanBias(metainfo=val_metainfo)
            )
        ])
    if test_loss is None:
        test_metainfo = MetricsMetaInfo(
            in_vars,
            out_vars,
            *data_module.get_lat_lon(),
            _get_climatology(data_module, "test")
        )
        test_loss = nn.ModuleList([
            RMSE(metainfo=test_metainfo),
            Denormalized(
                _get_denorm(data_module),
                Pearson(metainfo=test_metainfo)
            ),
            Denormalized(
                _get_denorm(data_module),
                MeanBias(metainfo=test_metainfo)
            )
        ])
    model_module = LitModule(net, optimizer, train_loss, val_loss, test_loss)
    return model_module

def _validate_model_kwargs(
    preset: Optional[str] = None,
    model: Optional[str] = None,
    model_kwargs: Optional[Dict[str, Any]] = None,
    net: Optional[torch.nn.Module] = None
):
    if (preset is None) and (model is None) and (net is None):
        raise RuntimeError("Please specify one of 'preset', 'model', or 'net'")
    if preset:
        if model is not None:
            warnings.warn("Ignoring 'model' since 'preset' was specified")
        model = preset
    if model and net:
        warnings.warn("Ignoring 'net' since one of 'preset' or 'model' was specified")
    if net and model_kwargs:
        warnings.warn("Ignoring 'model_kwargs' since 'net' was specified")
    return model

def _load_optimizer(
    net: torch.nn.Module,
    optim: Optional[str] = None,
    optim_kwargs: Optional[Dict[str, Any]] = None,
    lr_kwargs: Optional[Dict[str, Any]] = None,
    optimizer: Optional[Union[torch.optim.Optimizer, Dict[str, torch.optim.Optimizer]]] = None,
):
    if len(list(net.parameters())) == 0:
        warnings.warn("Net has no trainable parameters")
        return None
    if (optim is not None) and (optimizer is not None):
        raise RuntimeError("Please specify one of 'optim' or 'optimizer'")
    if optim and optimizer:
        warnings.warn("Ignoring 'optimizer' since 'optim' was specified")
    if optimizer and optim_kwargs:
        warnings.warn("Ignoring 'optim_kwargs' since 'optimizer' was specified")
    if lr_kwargs and optimizer:
        warnings.warn("Ignoring 'lr_kwargs' since 'optimizer was specified")
    if optim_kwargs is None:
        optim_kwargs = {}
    if optim.startswith("SGD"):
        optimizer = torch.optim.SGD(net.parameters(), **optim_kwargs)
    elif optim.startswith("Adam"):
        optimizer = torch.optim.Adam(net.parameters(), **optim_kwargs)
    elif optim.startswith("AdamW"):
        optimizer = torch.optim.AdamW(net.parameters(), **optim_kwargs)
    elif optim is not None:
        raise NotImplementedError(
            f"{optim} is not an implemented optimizer. If you think it should"
            " be, please raise an issue at"
            " https://github.com/aditya-grover/climate-learn/issues"
        )
    if optim.endswith("CosineAnnealingLR"):
        if lr_kwargs is None:
            lr_kwargs = {}
        lr_scheduler = LinearWarmupCosineAnnealingLR(optimizer, **lr_kwargs)
        optimizer = {
            "optimizer": optimizer,
            "lr_scheduler": lr_scheduler
        }
    return optimizer

def _get_climatology(data_module, split):
    clim = data_module.get_climatology(split=split)
    # Hotfix to work with dict style data
    clim = torch.stack(tuple(clim.values()))
    return clim

def _get_denorm(data_module):
    norm = data_module.get_out_transforms()
    # Hotfix to work with dict style data
    mean_norm = torch.tensor([norm[k].mean for k in norm.keys()])
    std_norm = torch.tensor([norm[k].std for k in norm.keys()])
    std_denorm = 1 / std_norm
    mean_denorm = -mean_norm * std_denorm
    return transforms.Normalize(mean_denorm, std_denorm)