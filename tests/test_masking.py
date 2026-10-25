import pytest

from cutweave.stages.composition.masking import MaskKeyframe, MaskRegion, MaskValidationError


def test_mask_filter_has_fixed_geometry_and_opacity():
    mask = MaskRegion(20, 40, 120, 80, 0.75)
    assert mask.filter_fragment(1080, 1920) == "drawbox=x=20:y=40:w=120:h=80:color=black@0.750:t=fill"


def test_mask_filter_can_be_limited_to_a_time_interval():
    mask = MaskRegion(20, 40, 120, 80, 1.0, start_ms=1250, end_ms=2750)
    assert mask.filter_fragment(1080, 1920).endswith(":enable='between(t\\,1.250\\,2.750)'")


def test_ellipse_and_polygon_compile_to_pixel_geometry_filters():
    ellipse = MaskRegion(10, 20, 80, 40, 0.5, shape="ellipse")
    polygon = MaskRegion(10, 10, 80, 60, 1.0, shape="polygon",
                         points=((10, 10), (90, 10), (50, 70)))
    assert "pow((X-(10+80/2))/(80/2),2)" in ellipse.filter_fragment(100, 100)
    assert "r(X,Y)*(1-0.500000)" in ellipse.filter_fragment(100, 100)
    assert "gt(abs(" in polygon.filter_fragment(100, 100)


def test_keyframes_use_linear_time_interpolation_for_geometry_and_opacity():
    mask = MaskRegion(0, 0, 20, 20, keyframes=(
        MaskKeyframe(0, 0, 0, 20, 20, 1),
        MaskKeyframe(1000, 60, 0, 40, 20, 0.5),
    ))
    expression = mask.filter_fragment(100, 100)
    assert "if(lt(T,1.000),(0+(60-0)*(T-0.000)/1.000),60)" in expression
    assert "1-if(lt(T,0.000),1,if(lt(T,1.000),(1+(0.5-1)*(T-0.000)/1.000),0.5))" in expression


@pytest.mark.parametrize("mask", [
    MaskRegion(-1, 0, 20, 20),
    MaskRegion(0, 0, 0, 20),
    MaskRegion(90, 90, 20, 20),
    MaskRegion(0, 0, 20, 20, opacity=1.1),
    MaskRegion(0, 0, 20, 20, start_ms=500, end_ms=500),
    MaskRegion(0, 0, 20, 20, shape="circle"),
    MaskRegion(0, 0, 20, 20, shape="polygon", points=((0, 0), (5, 5))),
    MaskRegion(0, 0, 20, 20, shape="polygon", points=((0, 0), (5, 5), (10, 10))),
    MaskRegion(0, 0, 20, 20, shape="polygon", points=((0, 0), (5, 5), (100, 10))),
    MaskRegion(0, 0, 20, 20, keyframes=(MaskKeyframe(100, 0, 0, 20, 20), MaskKeyframe(50, 0, 0, 20, 20))),
    MaskRegion(0, 0, 20, 20, keyframes=(MaskKeyframe(0, 0, 0, 20, 20), MaskKeyframe(0, 1, 1, 20, 20))),
    MaskRegion(0, 0, 20, 20, keyframes=(MaskKeyframe(0, 0, 0, 20, 20), MaskKeyframe(100, 90, 0, 20, 20))),
    MaskRegion(0, 0, 20, 20, keyframes=(MaskKeyframe(0, 0, 0, 20, 20, 0),)),
    MaskRegion(0, 0, 20, 20, effect="pixelate"),
    MaskRegion(0, 0, 20, 20, blur_radius=51),
])
def test_mask_regions_validate_geometry_opacity_and_time(mask):
    with pytest.raises(MaskValidationError):
        mask.validate(100, 100)
