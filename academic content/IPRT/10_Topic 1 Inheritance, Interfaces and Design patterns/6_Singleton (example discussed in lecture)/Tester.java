package singleton;

public class Tester {

	public static void main(String[] args) {
		// TODO Auto-generated method stub
		//Printer p=new Printer(); Can't create instance; private constructor
		Printer p1=Printer.getInstance();
		System.out.println(p1.toString());
		System.out.println(p1.hashCode());
		Printer p2=Printer.getInstance("Lab 1");
		System.out.println(p2.toString());
		System.out.println(p2.hashCode());
		
		
	}	

}
