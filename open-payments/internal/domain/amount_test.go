package domain

import "testing"

func TestAmountPrecision(t *testing.T) {
	a := Amount{Value: "9999999999999999999999999999999999999999999999", AssetCode: "USD", AssetScale: 2}
	b := a
	b.Value = "9999999999999999999999999999999999999999999998"
	cmp, e := a.Compare(b)
	if e != nil || cmp != 1 {
		t.Fatal("lost integer precision", e)
	}
	b.AssetScale = 3
	if _, e = a.Compare(b); e == nil {
		t.Fatal("accepted scale mismatch")
	}
}
func TestAmountInvalid(t *testing.T) {
	for _, v := range []string{"0", "-1", "1.00", "1e3", "01", "+2", "", " 3"} {
		a := Amount{Value: v, AssetCode: "USD", AssetScale: 2}
		if a.Validate(true) == nil {
			t.Fatalf("accepted %q", v)
		}
	}
	for _, scale := range []int{-1, 256} {
		if (Amount{Value: "1", AssetCode: "USD", AssetScale: scale}).Validate(true) == nil {
			t.Fatal("accepted invalid scale")
		}
	}
}
